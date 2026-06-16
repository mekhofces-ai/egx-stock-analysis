from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import Stock, StrategyBacktest, StrategyBacktestSummary, StrategyBacktestTrade
from app.services.market_data.base import ProviderChain, ProviderUnavailable, build_provider_chain
from app.services.strategies.cli_v6_egx import (
    MIN_CANDLES,
    STRATEGY_NAME,
    load_ohlcv_for_timeframe,
    normalize_symbol,
    normalize_timeframe,
    score_frame,
)


logger = logging.getLogger(__name__)
DEFAULT_COMMISSION = 0.0015
DEFAULT_SLIPPAGE = 0.002
BACKTEST_TIMEFRAMES = ["15m", "1h", "4h", "1d"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class CliV6Trade:
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    bars_held: int


def _to_dt(value: Any) -> datetime:
    return pd.to_datetime(value).to_pydatetime()


def _single_timeframe_signal(frame_slice: pd.DataFrame, timeframe: str) -> tuple[str, dict[str, Any]]:
    scored = score_frame(frame_slice, timeframe)
    status = scored.get("status")
    if status == "BULLISH":
        return "STRONG BUY", scored
    if status == "BEARISH":
        return "STRONG SELL", scored
    return "NEUTRAL", scored


def _max_drawdown(equity_curve: list[dict[str, Any]]) -> float:
    if not equity_curve:
        return 0.0
    values = pd.Series([point["equity"] for point in equity_curve], dtype=float)
    if values.empty:
        return 0.0
    drawdown = ((values / values.cummax()) - 1.0).min() * 100
    return round(abs(float(drawdown)), 2)


def _run_no_lookahead(
    frame: pd.DataFrame,
    timeframe: str,
    commission: float,
    slippage: float,
) -> tuple[list[CliV6Trade], list[dict[str, Any]], str]:
    trades: list[CliV6Trade] = []
    equity = 100.0
    equity_curve = [{"time": _to_dt(frame.iloc[0]["date"]).isoformat(), "equity": equity}]
    open_trade: dict[str, Any] | None = None
    latest_signal = "INSUFFICIENT DATA"
    start_idx = min(max(MIN_CANDLES, 55), max(MIN_CANDLES, len(frame) - 2))

    for idx in range(start_idx, len(frame) - 1):
        history = frame.iloc[: idx + 1].copy()
        try:
            signal, scored = _single_timeframe_signal(history, timeframe)
            latest_signal = signal
        except Exception:
            continue
        next_bar = frame.iloc[idx + 1]
        next_open = float(next_bar["open"])

        if open_trade:
            if signal in {"STRONG SELL", "WEAK SELL"}:
                exit_price = next_open * (1 - slippage)
                pnl_pct = ((exit_price - open_trade["entry_price"]) / open_trade["entry_price"]) - (commission * 2)
                pnl = exit_price - open_trade["entry_price"] - (open_trade["entry_price"] + exit_price) * commission
                equity *= 1 + pnl_pct
                trade = CliV6Trade(
                    entry_date=open_trade["entry_date"],
                    exit_date=_to_dt(next_bar["date"]),
                    entry_price=round(open_trade["entry_price"], 4),
                    exit_price=round(exit_price, 4),
                    pnl=round(pnl, 4),
                    pnl_pct=round(pnl_pct * 100, 2),
                    exit_reason=signal,
                    bars_held=idx + 1 - open_trade["entry_idx"],
                )
                trades.append(trade)
                equity_curve.append({"time": trade.exit_date.isoformat(), "equity": round(equity, 4)})
                open_trade = None
            continue

        if signal == "STRONG BUY":
            open_trade = {
                "entry_idx": idx + 1,
                "entry_date": _to_dt(next_bar["date"]),
                "entry_price": next_open * (1 + slippage),
                "signal_score": scored.get("score"),
            }

    if open_trade:
        last = frame.iloc[-1]
        exit_price = float(last["close"]) * (1 - slippage)
        pnl_pct = ((exit_price - open_trade["entry_price"]) / open_trade["entry_price"]) - (commission * 2)
        pnl = exit_price - open_trade["entry_price"] - (open_trade["entry_price"] + exit_price) * commission
        equity *= 1 + pnl_pct
        trade = CliV6Trade(
            entry_date=open_trade["entry_date"],
            exit_date=_to_dt(last["date"]),
            entry_price=round(open_trade["entry_price"], 4),
            exit_price=round(exit_price, 4),
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct * 100, 2),
            exit_reason="open_marked_to_market",
            bars_held=len(frame) - 1 - open_trade["entry_idx"],
        )
        trades.append(trade)
        equity_curve.append({"time": trade.exit_date.isoformat(), "equity": round(equity, 4)})

    if latest_signal == "INSUFFICIENT DATA" and len(frame) >= MIN_CANDLES:
        try:
            latest_signal, _ = _single_timeframe_signal(frame, timeframe)
        except Exception:
            pass
    return trades, equity_curve, latest_signal


def _metrics(frame: pd.DataFrame, trades: list[CliV6Trade], equity_curve: list[dict[str, Any]], latest_signal: str) -> dict[str, Any]:
    returns = [trade.pnl_pct for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    final_equity = float(equity_curve[-1]["equity"]) if equity_curve else 100.0
    latest_recommendation = latest_signal
    return {
        "start_date": _to_dt(frame.iloc[0]["date"]).isoformat(),
        "end_date": _to_dt(frame.iloc[-1]["date"]).isoformat(),
        "total_return": round(final_equity - 100.0, 2),
        "max_drawdown": _max_drawdown(equity_curve),
        "win_rate": round(len(wins) / len(returns) * 100, 2) if returns else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "trades_count": len(trades),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "best_trade": round(max(returns), 2) if returns else None,
        "worst_trade": round(min(returns), 2) if returns else None,
        "latest_signal": latest_signal,
        "latest_recommendation": latest_recommendation,
    }


def _score_backtest(metrics: dict[str, Any]) -> tuple[float, str]:
    trades = int(metrics.get("trades_count") or 0)
    if trades == 0:
        return 35.0, "NEUTRAL"
    total_return = float(metrics.get("total_return") or 0)
    win_rate = float(metrics.get("win_rate") or 0)
    max_drawdown = float(metrics.get("max_drawdown") or 0)
    profit_factor = metrics.get("profit_factor")
    pf_score = min(float(profit_factor or 0), 3.0) * 8
    score = 45 + total_return * 0.55 + (win_rate - 50) * 0.25 - max_drawdown * 0.55 + pf_score + min(trades, 25) * 0.3
    score = round(max(0.0, min(100.0, score)), 2)
    if score >= 75 and total_return > 0 and (profit_factor or 0) >= 1.15:
        return score, "BUY"
    if score >= 60 and total_return > -2:
        return score, "WATCH"
    if score < 40 or total_return < -10:
        return score, "AVOID"
    return score, "NEUTRAL"


def _persist_backtest(
    db: Session,
    symbol: str,
    timeframe: str,
    run_id: str,
    provider: str,
    metrics: dict[str, Any],
    trades: list[CliV6Trade],
    equity_curve: list[dict[str, Any]],
    commission: float,
    slippage: float,
) -> StrategyBacktest:
    score, recommendation = _score_backtest(metrics)
    started_at = utcnow()
    summary_json = {
        "strategy_name": STRATEGY_NAME,
        "timeframe": timeframe,
        "provider": provider,
        "commission": commission,
        "slippage": slippage,
        "metrics": metrics,
        "score": score,
        "recommendation": recommendation,
        "equity_curve": equity_curve,
        "lookahead_review": "Signal is calculated on completed candle; fill is at next candle open.",
        "risk_note": RISK_NOTE,
        "run_id": run_id,
    }
    backtest = StrategyBacktest(
        symbol=symbol,
        strategy_name=STRATEGY_NAME,
        timeframe=timeframe,
        provider=provider,
        data_quality="fresh",
        total_return_pct=metrics.get("total_return"),
        max_drawdown_pct=metrics.get("max_drawdown"),
        win_rate=metrics.get("win_rate"),
        avg_win_pct=metrics.get("avg_win"),
        avg_loss_pct=metrics.get("avg_loss"),
        profit_factor=metrics.get("profit_factor"),
        trades_count=int(metrics.get("trades_count") or 0),
        best_trade_pct=metrics.get("best_trade"),
        worst_trade_pct=metrics.get("worst_trade"),
        latest_signal=metrics.get("latest_signal"),
        equity_curve=equity_curve,
        summary_json=summary_json,
        started_at=started_at,
        completed_at=utcnow(),
    )
    db.add(backtest)
    db.flush()
    for trade in trades:
        db.add(
            StrategyBacktestTrade(
                backtest_id=backtest.id,
                symbol=symbol,
                strategy_name=STRATEGY_NAME,
                timeframe=timeframe,
                entry_time=trade.entry_date,
                exit_time=trade.exit_date,
                entry_date=trade.entry_date.isoformat(),
                exit_date=trade.exit_date.isoformat(),
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                pnl=trade.pnl,
                pnl_pct=trade.pnl_pct,
                return_pct=trade.pnl_pct,
                reason=trade.exit_reason,
                exit_reason=trade.exit_reason,
                run_id=run_id,
                bars_held=trade.bars_held,
            )
        )
    summary = db.scalar(
        select(StrategyBacktestSummary).where(
            StrategyBacktestSummary.symbol == symbol,
            StrategyBacktestSummary.strategy_name == STRATEGY_NAME,
            StrategyBacktestSummary.timeframe == timeframe,
        )
    )
    if summary:
        summary.score = score
        summary.recommendation = recommendation
        summary.start_date = metrics.get("start_date")
        summary.end_date = metrics.get("end_date")
        summary.total_return = metrics.get("total_return")
        summary.max_drawdown = metrics.get("max_drawdown")
        summary.win_rate = metrics.get("win_rate")
        summary.profit_factor = metrics.get("profit_factor")
        summary.trades_count = metrics.get("trades_count")
        summary.avg_win = metrics.get("avg_win")
        summary.avg_loss = metrics.get("avg_loss")
        summary.best_trade = metrics.get("best_trade")
        summary.worst_trade = metrics.get("worst_trade")
        summary.latest_signal = metrics.get("latest_signal")
        summary.latest_recommendation = metrics.get("latest_recommendation")
        summary.run_id = run_id
        summary.summary_json = summary_json
        summary.updated_at = utcnow()
    else:
        db.add(
            StrategyBacktestSummary(
                symbol=symbol,
                strategy_name=STRATEGY_NAME,
                timeframe=timeframe,
                score=score,
                recommendation=recommendation,
                start_date=metrics.get("start_date"),
                end_date=metrics.get("end_date"),
                total_return=metrics.get("total_return"),
                max_drawdown=metrics.get("max_drawdown"),
                win_rate=metrics.get("win_rate"),
                profit_factor=metrics.get("profit_factor"),
                trades_count=metrics.get("trades_count"),
                avg_win=metrics.get("avg_win"),
                avg_loss=metrics.get("avg_loss"),
                best_trade=metrics.get("best_trade"),
                worst_trade=metrics.get("worst_trade"),
                latest_signal=metrics.get("latest_signal"),
                latest_recommendation=metrics.get("latest_recommendation"),
                run_id=run_id,
                created_at=utcnow(),
                summary_json=summary_json,
            )
        )
    return backtest


def run_cli_v6_backtest_symbol(
    db: Session,
    symbol: str,
    timeframe: str = "1d",
    settings: Settings | None = None,
    provider_chain: ProviderChain | None = None,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
    run_id: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    provider_chain = provider_chain or build_provider_chain(settings)
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    run_id = run_id or f"bt_cli_v6_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    frame = load_ohlcv_for_timeframe(db, symbol, timeframe, settings=settings, provider_chain=provider_chain)
    provider = str(frame.attrs.get("provider", "unknown"))
    frame = frame.sort_values("date").reset_index(drop=True)
    if len(frame) < MIN_CANDLES + 2:
        raise ProviderUnavailable(f"Backtest needs at least {MIN_CANDLES + 2} completed candles for {symbol} {timeframe}.")
    trades, equity_curve, latest_signal = _run_no_lookahead(frame, timeframe, commission=commission, slippage=slippage)
    metrics = _metrics(frame, trades, equity_curve, latest_signal)
    score, recommendation = _score_backtest(metrics)
    with sqlite_write_lock():
        backtest = _persist_backtest(
            db,
            symbol=symbol,
            timeframe=timeframe,
            run_id=run_id,
            provider=provider,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            commission=commission,
            slippage=slippage,
        )
        db.commit()
    return {
        "symbol": symbol,
        "strategy_name": STRATEGY_NAME,
        "timeframe": timeframe,
        "start_date": metrics.get("start_date"),
        "end_date": metrics.get("end_date"),
        "number_of_trades": metrics.get("trades_count"),
        "trades_count": metrics.get("trades_count"),
        "win_rate": metrics.get("win_rate"),
        "average_win": metrics.get("avg_win"),
        "average_loss": metrics.get("avg_loss"),
        "avg_win": metrics.get("avg_win"),
        "avg_loss": metrics.get("avg_loss"),
        "profit_factor": metrics.get("profit_factor"),
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "best_trade": metrics.get("best_trade"),
        "worst_trade": metrics.get("worst_trade"),
        "latest_signal": metrics.get("latest_signal"),
        "latest_recommendation": metrics.get("latest_recommendation"),
        "score": score,
        "recommendation": recommendation,
        "equity_curve": equity_curve,
        "backtest_id": backtest.id,
        "run_id": run_id,
        "commission": commission,
        "slippage": slippage,
        "risk_note": RISK_NOTE,
    }


def run_cli_v6_backtest_universe(
    db: Session,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    limit: int | None = None,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
) -> dict[str, Any]:
    settings = settings or get_settings()
    selected = [normalize_symbol(symbol) for symbol in symbols] if symbols else [
        normalize_symbol(symbol)
        for symbol in db.scalars(
            select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc()).limit(limit or settings.strategy_symbol_limit)
        ).all()
    ]
    frames = [normalize_timeframe(frame) for frame in (timeframes or BACKTEST_TIMEFRAMES)]
    provider_chain = build_provider_chain(settings)
    run_id = f"bt_cli_v6_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in selected:
        for timeframe in frames:
            try:
                rows.append(
                    run_cli_v6_backtest_symbol(
                        db,
                        symbol,
                        timeframe=timeframe,
                        settings=settings,
                        provider_chain=provider_chain,
                        commission=commission,
                        slippage=slippage,
                        run_id=run_id,
                    )
                )
            except Exception as exc:
                logger.warning("CLI v6 backtest skipped for %s %s: %s", symbol, timeframe, exc)
                errors.append(f"{symbol} {timeframe}: {exc}")
    rows.sort(key=lambda row: (row.get("score") or 0, row.get("total_return") or -999), reverse=True)
    return {
        "run_id": run_id,
        "strategy_name": STRATEGY_NAME,
        "rows": rows,
        "errors": errors,
        "risk_note": RISK_NOTE,
    }


def get_latest_cli_v6_backtest_summary(db: Session, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    stmt = select(StrategyBacktestSummary).where(StrategyBacktestSummary.strategy_name == STRATEGY_NAME)
    if symbol:
        stmt = stmt.where(StrategyBacktestSummary.symbol == normalize_symbol(symbol))
    summaries = db.scalars(stmt.order_by(StrategyBacktestSummary.updated_at.desc()).limit(limit)).all()
    rows: list[dict[str, Any]] = []
    for item in summaries:
        payload = item.summary_json or {}
        metrics = payload.get("metrics") or {}
        rows.append(
            {
                "symbol": item.symbol,
                "strategy_name": item.strategy_name,
                "timeframe": item.timeframe,
                "start_date": item.start_date or metrics.get("start_date"),
                "end_date": item.end_date or metrics.get("end_date"),
                "total_return": item.total_return if item.total_return is not None else metrics.get("total_return"),
                "max_drawdown": item.max_drawdown if item.max_drawdown is not None else metrics.get("max_drawdown"),
                "win_rate": item.win_rate if item.win_rate is not None else metrics.get("win_rate"),
                "profit_factor": item.profit_factor if item.profit_factor is not None else metrics.get("profit_factor"),
                "trades_count": item.trades_count if item.trades_count is not None else metrics.get("trades_count"),
                "avg_win": item.avg_win if item.avg_win is not None else metrics.get("avg_win"),
                "avg_loss": item.avg_loss if item.avg_loss is not None else metrics.get("avg_loss"),
                "best_trade": item.best_trade if item.best_trade is not None else metrics.get("best_trade"),
                "worst_trade": item.worst_trade if item.worst_trade is not None else metrics.get("worst_trade"),
                "latest_signal": item.latest_signal or metrics.get("latest_signal"),
                "latest_recommendation": item.latest_recommendation or metrics.get("latest_recommendation"),
                "score": item.score,
                "recommendation": item.recommendation,
                "run_id": item.run_id or payload.get("run_id"),
                "updated_at": item.updated_at,
                "summary_json": payload,
            }
        )
    return rows


def format_cli_v6_backtest_report(db: Session, symbol: str, settings: Settings | None = None) -> str:
    symbol = normalize_symbol(symbol)
    rows = get_latest_cli_v6_backtest_summary(db, symbol=symbol, limit=10)
    if not rows:
        try:
            run_cli_v6_backtest_symbol(db, symbol, timeframe="1d", settings=settings or get_settings())
            rows = get_latest_cli_v6_backtest_summary(db, symbol=symbol, limit=10)
        except Exception as exc:
            return (
                f"CLI v6 Backtest: {symbol}\n"
                f"No backtest could run because real OHLCV is missing or insufficient: {exc}\n"
                "Run TradingView/data import first.\n"
                f"Risk Note: {RISK_NOTE}"
            )
    rows.sort(key=lambda row: row.get("score") or 0, reverse=True)
    best = rows[0]
    lines = [
        f"CLI v6 Backtest: {symbol}",
        f"Strategy: {STRATEGY_NAME}",
        f"Best timeframe: {best.get('timeframe')} | recommendation {best.get('recommendation')} | score {float(best.get('score') or 0):.0f}%",
        f"Total return: {best.get('total_return') if best.get('total_return') is not None else '-'}%",
        f"Win rate: {best.get('win_rate') if best.get('win_rate') is not None else '-'}%",
        f"Max drawdown: {best.get('max_drawdown') if best.get('max_drawdown') is not None else '-'}%",
        f"Profit factor: {best.get('profit_factor') if best.get('profit_factor') is not None else '-'}",
        f"Trades count: {best.get('trades_count') or 0}",
        f"Latest signal: {best.get('latest_signal') or '-'}",
        f"Latest recommendation: {best.get('latest_recommendation') or '-'}",
        "",
        "Frames",
    ]
    for row in rows[:4]:
        lines.append(
            f"- {row.get('timeframe')}: {row.get('recommendation')} | return {row.get('total_return') if row.get('total_return') is not None else '-'}% | "
            f"WR {row.get('win_rate') if row.get('win_rate') is not None else '-'}% | DD {row.get('max_drawdown') if row.get('max_drawdown') is not None else '-'}%"
        )
    lines.extend(
        [
            "",
            "Assumptions: entries/exits fill at next candle open; commission and slippage are included.",
            f"Risk Note: {RISK_NOTE}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CLI v6 EGX backtest.")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--timeframe", type=str, default=None)
    parser.add_argument("--timeframes", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    with SessionLocal() as db:
        if args.symbol:
            frames = [normalize_timeframe(args.timeframe)] if args.timeframe else ["1d"]
            results = [
                run_cli_v6_backtest_symbol(db, args.symbol, timeframe=frame, commission=args.commission, slippage=args.slippage)
                for frame in frames
            ]
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
            else:
                print(format_cli_v6_backtest_report(db, args.symbol))
            return
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
        frames = [item.strip() for item in args.timeframes.split(",") if item.strip()] if args.timeframes else BACKTEST_TIMEFRAMES
        result = run_cli_v6_backtest_universe(
            db,
            symbols=symbols,
            timeframes=frames,
            limit=args.limit,
            commission=args.commission,
            slippage=args.slippage,
        )
        print(f"CLI v6 backtest run {result['run_id']} completed: {len(result['rows'])} rows.")
        for row in result["rows"][:10]:
            print(
                f"{row['symbol']} {row['timeframe']} {row.get('recommendation')} "
                f"score={row.get('score') or 0:.0f} return={row.get('total_return')}"
            )


if __name__ == "__main__":
    main()
