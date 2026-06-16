from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DISCLAIMER, Settings, get_settings
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import Stock, StrategyBacktest, StrategyBacktestSummary, StrategyBacktestTrade
from app.services.market_data.base import ProviderUnavailable, build_provider_chain
from app.services.strategy import (
    _action_from_score,
    _load_strategy_frame,
    _max_hold_bars,
    _reference_quote,
    _risk_levels,
    _signal_score,
    normalize_timeframe,
)


logger = logging.getLogger(__name__)
STRATEGY_NAME = "multi_timeframe_ema_rsi_macd"
DEFAULT_COMMISSION_BPS = 15.0
DEFAULT_SLIPPAGE_BPS = 20.0


@dataclass
class ReviewedTrade:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    reason: str
    bars_held: int


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _to_dt(value: Any) -> datetime:
    return pd.to_datetime(value).to_pydatetime()


def _annualized_return(total_return_pct: float, start: datetime, end: datetime) -> float | None:
    days = max((end - start).total_seconds() / 86400, 0.0)
    if days < 20:
        return None
    try:
        return round(((1 + total_return_pct / 100) ** (365.0 / days) - 1) * 100, 2)
    except Exception:
        return None


def _sharpe_like(returns: list[float]) -> float | None:
    if len(returns) < 3:
        return None
    series = np.array(returns, dtype=float) / 100.0
    std = float(np.std(series, ddof=1))
    if std == 0:
        return None
    return round(float(np.mean(series) / std * np.sqrt(len(series))), 3)


def _max_drawdown_pct(equity_points: list[dict[str, Any]]) -> float:
    values = pd.Series([point["equity"] for point in equity_points], dtype=float)
    if values.empty:
        return 0.0
    drawdown = ((values / values.cummax()) - 1.0).min() * 100
    return round(abs(float(drawdown)), 2)


def _run_no_lookahead_backtest(
    frame: pd.DataFrame,
    timeframe: str,
    commission_bps: float = DEFAULT_COMMISSION_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> tuple[list[ReviewedTrade], list[dict[str, Any]]]:
    trades: list[ReviewedTrade] = []
    equity = 100.0
    start_time = _to_dt(frame.iloc[0]["date"])
    equity_curve = [{"time": start_time.isoformat(), "equity": round(equity, 4)}]
    open_trade: dict[str, Any] | None = None
    max_hold = _max_hold_bars(timeframe)
    start_idx = min(60, max(20, len(frame) // 3))
    slip = slippage_bps / 10_000
    commission_round_trip_pct = (commission_bps * 2) / 100

    for idx in range(start_idx, len(frame)):
        row = frame.iloc[idx]
        prev = frame.iloc[idx - 1]

        if open_trade:
            exit_price = None
            reason = ""
            low = float(row["low"])
            high = float(row["high"])
            if low <= open_trade["stop"] and high >= open_trade["target"]:
                exit_price = open_trade["stop"] * (1 - slip)
                reason = "stop_and_target_same_candle_conservative_stop"
            elif low <= open_trade["stop"]:
                exit_price = open_trade["stop"] * (1 - slip)
                reason = "stop"
            elif high >= open_trade["target"]:
                exit_price = open_trade["target"] * (1 - slip)
                reason = "target"
            else:
                score, _, trend = _signal_score(row, prev)
                if trend == "BEARISH" or score < 42:
                    exit_price = float(row["close"]) * (1 - slip)
                    reason = "trend_exit"
                elif idx - open_trade["entry_idx"] >= max_hold:
                    exit_price = float(row["close"]) * (1 - slip)
                    reason = "time_exit"

            if exit_price is not None:
                net_return_pct = ((exit_price - open_trade["entry_price"]) / open_trade["entry_price"] * 100) - commission_round_trip_pct
                equity *= 1 + net_return_pct / 100
                exit_time = _to_dt(row["date"])
                trades.append(
                    ReviewedTrade(
                        entry_time=open_trade["entry_time"],
                        exit_time=exit_time,
                        entry_price=round(open_trade["entry_price"], 4),
                        exit_price=round(exit_price, 4),
                        return_pct=round(net_return_pct, 2),
                        reason=reason,
                        bars_held=idx - open_trade["entry_idx"],
                    )
                )
                equity_curve.append({"time": exit_time.isoformat(), "equity": round(equity, 4)})
                open_trade = None
            continue

        if idx >= len(frame) - 1:
            continue
        score, notes, trend = _signal_score(row, prev)
        if _action_from_score(score, trend, notes) == "BUY":
            _, stop, target = _risk_levels(row)
            next_row = frame.iloc[idx + 1]
            open_trade = {
                "entry_idx": idx + 1,
                "entry_time": _to_dt(next_row["date"]),
                "entry_price": float(next_row["open"]) * (1 + slip),
                "stop": stop,
                "target": target,
            }

    if open_trade:
        row = frame.iloc[-1]
        exit_price = float(row["close"]) * (1 - slip)
        net_return_pct = ((exit_price - open_trade["entry_price"]) / open_trade["entry_price"] * 100) - commission_round_trip_pct
        equity *= 1 + net_return_pct / 100
        exit_time = _to_dt(row["date"])
        trades.append(
            ReviewedTrade(
                entry_time=open_trade["entry_time"],
                exit_time=exit_time,
                entry_price=round(open_trade["entry_price"], 4),
                exit_price=round(exit_price, 4),
                return_pct=round(net_return_pct, 2),
                reason="open_marked_to_market",
                bars_held=len(frame) - 1 - open_trade["entry_idx"],
            )
        )
        equity_curve.append({"time": exit_time.isoformat(), "equity": round(equity, 4)})

    return trades, equity_curve


def _metrics(
    frame: pd.DataFrame,
    trades: list[ReviewedTrade],
    equity_curve: list[dict[str, Any]],
    latest_signal: str,
) -> dict[str, Any]:
    returns = [trade.return_pct for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    final_equity = float(equity_curve[-1]["equity"]) if equity_curve else 100.0
    total_return_pct = round(final_equity - 100.0, 2)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    start = _to_dt(frame.iloc[0]["date"])
    end = _to_dt(frame.iloc[-1]["date"])
    return {
        "total_return_pct": total_return_pct,
        "annualized_return_pct": _annualized_return(total_return_pct, start, end),
        "sharpe_like": _sharpe_like(returns),
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "win_rate": round(len(wins) / len(returns) * 100, 2) if returns else None,
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "trades_count": len(trades),
        "best_trade_pct": round(max(returns), 2) if returns else None,
        "worst_trade_pct": round(min(returns), 2) if returns else None,
        "latest_signal": latest_signal,
    }


def _score_backtest(metrics: dict[str, Any]) -> tuple[float, str]:
    trades = int(metrics.get("trades_count") or 0)
    if trades == 0:
        return 35.0, "NEUTRAL"
    total_return = float(metrics.get("total_return_pct") or 0)
    win_rate = float(metrics.get("win_rate") or 0)
    drawdown = float(metrics.get("max_drawdown_pct") or 0)
    profit_factor = metrics.get("profit_factor")
    profit_factor_score = min(float(profit_factor or 0), 3.0) * 8
    score = 45 + total_return * 0.55 + (win_rate - 50) * 0.25 - drawdown * 0.55 + profit_factor_score + min(trades, 20) * 0.3
    score = _bound(score)
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
    provider: str,
    data_quality: str,
    metrics: dict[str, Any],
    trades: list[ReviewedTrade],
    equity_curve: list[dict[str, Any]],
    summary_json: dict[str, Any],
    started_at: datetime,
) -> StrategyBacktest:
    backtest = StrategyBacktest(
        symbol=symbol,
        strategy_name=STRATEGY_NAME,
        timeframe=timeframe,
        provider=provider,
        data_quality=data_quality,
        total_return_pct=metrics.get("total_return_pct"),
        annualized_return_pct=metrics.get("annualized_return_pct"),
        sharpe_like=metrics.get("sharpe_like"),
        max_drawdown_pct=metrics.get("max_drawdown_pct"),
        win_rate=metrics.get("win_rate"),
        avg_win_pct=metrics.get("avg_win_pct"),
        avg_loss_pct=metrics.get("avg_loss_pct"),
        profit_factor=metrics.get("profit_factor"),
        trades_count=int(metrics.get("trades_count") or 0),
        best_trade_pct=metrics.get("best_trade_pct"),
        worst_trade_pct=metrics.get("worst_trade_pct"),
        latest_signal=metrics.get("latest_signal"),
        equity_curve=equity_curve,
        summary_json=summary_json,
        started_at=started_at,
        completed_at=datetime.utcnow(),
    )
    db.add(backtest)
    db.flush()
    for trade in trades:
        db.add(
            StrategyBacktestTrade(
                backtest_id=backtest.id,
                symbol=symbol,
                timeframe=timeframe,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                return_pct=trade.return_pct,
                reason=trade.reason,
                bars_held=trade.bars_held,
            )
        )

    score, recommendation = _score_backtest(metrics)
    summary = db.scalar(
        select(StrategyBacktestSummary).where(
            StrategyBacktestSummary.symbol == symbol,
            StrategyBacktestSummary.strategy_name == STRATEGY_NAME,
            StrategyBacktestSummary.timeframe == timeframe,
        )
    )
    payload = {**summary_json, "metrics": metrics, "latest_backtest_id": backtest.id}
    if summary:
        summary.score = score
        summary.recommendation = recommendation
        summary.summary_json = payload
        summary.updated_at = datetime.utcnow()
    else:
        db.add(
            StrategyBacktestSummary(
                symbol=symbol,
                strategy_name=STRATEGY_NAME,
                timeframe=timeframe,
                score=score,
                recommendation=recommendation,
                summary_json=payload,
            )
        )
    return backtest


def review_strategy_rules() -> dict[str, Any]:
    return {
        "strategy_name": STRATEGY_NAME,
        "rules": [
            "Trend: close above EMA20 and EMA50, with EMA20 rising.",
            "Momentum: RSI should be constructive, MACD above signal is positive, overbought RSI is penalized.",
            "Volume: volume above the 20-candle average improves score; weak volume reduces score.",
            "Breakout: close above prior 20-candle resistance adds confirmation.",
            "Risk: ATR/support-derived stop and 2R target are used for BUY/WATCH setups.",
        ],
        "bias_review": [
            "Reviewed engine calculates the signal on a completed candle and enters on the next candle open.",
            "Intrabar stop/target handling is conservative: if both are touched in one candle, stop is assumed first.",
            "Commission and slippage are included before metrics are persisted.",
            "The strategy is rule-based and may not fit every EGX stock equally; ranking uses realized backtest quality by symbol/timeframe.",
        ],
        "risk_note": "Backtests are historical simulations, not financial advice. Use position sizing and stop loss.",
    }


def run_symbol_backtest(
    symbol: str,
    timeframes: list[str] | None = None,
    db: Session | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    frames = [normalize_timeframe(item) for item in (timeframes or settings.strategy_timeframe_list)]
    symbol = symbol.upper().replace("EGX:", "").strip()

    def _run(active_db: Session) -> dict[str, Any]:
        logger.info("Backtest started for %s on %s.", symbol, frames)
        provider_chain = build_provider_chain(settings)
        reference_quote = _reference_quote(provider_chain, symbol)
        rows: list[dict[str, Any]] = []
        for timeframe in frames:
            started_at = datetime.utcnow()
            try:
                frame, provider, is_mock, data_quality, reference_price, price_diff = _load_strategy_frame(
                    provider_chain,
                    symbol,
                    timeframe,
                    settings,
                    reference_quote,
                )
                last = frame.iloc[-1]
                prev = frame.iloc[-2]
                latest_score, notes, latest_trend = _signal_score(last, prev)
                latest_signal = _action_from_score(latest_score, latest_trend, notes)
                trades, equity_curve = _run_no_lookahead_backtest(frame, timeframe)
                metrics = _metrics(frame, trades, equity_curve, latest_signal)
                summary_json = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "provider": provider,
                    "is_mock": is_mock,
                    "data_quality": data_quality,
                    "reference_price": float(reference_price) if reference_price is not None else None,
                    "price_difference_percent": price_diff,
                    "latest_score": round(latest_score, 2),
                    "latest_trend": latest_trend,
                    "latest_notes": notes[:5],
                    "commission_bps": DEFAULT_COMMISSION_BPS,
                    "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                    "lookahead_review": "Signal uses closed candle; fill occurs at next candle open.",
                }
                with sqlite_write_lock():
                    backtest = _persist_backtest(
                        active_db,
                        symbol=symbol,
                        timeframe=timeframe,
                        provider=provider,
                        data_quality=data_quality,
                        metrics=metrics,
                        trades=trades,
                        equity_curve=equity_curve,
                        summary_json=summary_json,
                        started_at=started_at,
                    )
                    active_db.commit()
                score, recommendation = _score_backtest(metrics)
                rows.append(
                    {
                        **summary_json,
                        **metrics,
                        "backtest_id": backtest.id,
                        "score": score,
                        "recommendation": recommendation,
                        "error": None,
                    }
                )
            except Exception as exc:
                logger.exception("Backtest failed for %s %s", symbol, timeframe)
                metrics = {
                    "total_return_pct": None,
                    "annualized_return_pct": None,
                    "sharpe_like": None,
                    "max_drawdown_pct": None,
                    "win_rate": None,
                    "avg_win_pct": None,
                    "avg_loss_pct": None,
                    "profit_factor": None,
                    "trades_count": 0,
                    "best_trade_pct": None,
                    "worst_trade_pct": None,
                    "latest_signal": "UNAVAILABLE",
                }
                summary_json = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "provider": "unavailable",
                    "data_quality": "unavailable",
                    "error": str(exc),
                    "lookahead_review": "No simulation was run because trusted candles were unavailable.",
                }
                with sqlite_write_lock():
                    backtest = _persist_backtest(
                        active_db,
                        symbol=symbol,
                        timeframe=timeframe,
                        provider="unavailable",
                        data_quality="unavailable",
                        metrics=metrics,
                        trades=[],
                        equity_curve=[],
                        summary_json=summary_json,
                        started_at=started_at,
                    )
                    active_db.commit()
                rows.append({**summary_json, **metrics, "backtest_id": backtest.id, "score": 0.0, "recommendation": "UNAVAILABLE"})
        rows.sort(key=lambda row: (row.get("score") or 0, row.get("total_return_pct") or -999), reverse=True)
        logger.info("Backtest completed for %s.", symbol)
        return {
            "symbol": symbol,
            "strategy_name": STRATEGY_NAME,
            "generated_at": datetime.utcnow().isoformat(),
            "timeframes": rows,
            "review": review_strategy_rules(),
            "disclaimer": DISCLAIMER,
        }

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def _candidate_symbols(db: Session, limit: int) -> list[str]:
    return db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol).limit(limit)).all()


def run_universe_backtests(
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        selected = [item.upper().replace("EGX:", "") for item in symbols] if symbols else _candidate_symbols(active_db, limit or settings.strategy_symbol_limit)
        results = [run_symbol_backtest(symbol, db=active_db, settings=settings) for symbol in selected]
        summary_rows: list[dict[str, Any]] = []
        for result in results:
            best = max(result["timeframes"], key=lambda row: row.get("score") or 0) if result["timeframes"] else None
            if best:
                summary_rows.append(
                    {
                        "symbol": result["symbol"],
                        "best_timeframe": best.get("timeframe"),
                        "score": best.get("score"),
                        "recommendation": best.get("recommendation"),
                        "total_return_pct": best.get("total_return_pct"),
                        "win_rate": best.get("win_rate"),
                        "max_drawdown_pct": best.get("max_drawdown_pct"),
                        "profit_factor": best.get("profit_factor"),
                        "trades_count": best.get("trades_count"),
                    }
                )
        summary_rows.sort(key=lambda row: (row.get("score") or 0, row.get("total_return_pct") or -999), reverse=True)
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "strategy_name": STRATEGY_NAME,
            "rows": summary_rows,
            "details": results,
            "review": review_strategy_rules(),
            "disclaimer": DISCLAIMER,
        }

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def get_latest_backtest_summary(db: Session, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    stmt = select(StrategyBacktestSummary)
    if symbol:
        stmt = stmt.where(StrategyBacktestSummary.symbol == symbol.upper().replace("EGX:", ""))
    summaries = db.scalars(stmt.order_by(StrategyBacktestSummary.updated_at.desc()).limit(limit)).all()
    rows = []
    for item in summaries:
        payload = item.summary_json or {}
        metrics = payload.get("metrics") or {}
        rows.append(
            {
                "symbol": item.symbol,
                "strategy_name": item.strategy_name,
                "timeframe": item.timeframe,
                "score": item.score,
                "recommendation": item.recommendation,
                "updated_at": item.updated_at,
                "total_return_pct": metrics.get("total_return_pct"),
                "win_rate": metrics.get("win_rate"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "profit_factor": metrics.get("profit_factor"),
                "trades_count": metrics.get("trades_count"),
                "latest_signal": metrics.get("latest_signal"),
                "summary_json": payload,
            }
        )
    return rows


def format_backtest_report(db: Session, symbol: str) -> str:
    rows = get_latest_backtest_summary(db, symbol=symbol, limit=10)
    symbol = symbol.upper().replace("EGX:", "")
    if not rows:
        return f"No stored backtest found for {symbol}. Run: python app/services/backtest_engine.py --symbol {symbol}\nDisclaimer: {DISCLAIMER}"
    rows.sort(key=lambda row: row.get("score") or 0, reverse=True)
    best = rows[0]
    lines = [
        f"EGX Strategy Backtest: {symbol}",
        f"Strategy: {best.get('strategy_name')}",
        f"Best timeframe: {best.get('timeframe')} | recommendation {best.get('recommendation')} | score {best.get('score') or 0:.0f}%",
        f"Total return: {best.get('total_return_pct') if best.get('total_return_pct') is not None else '-'}%",
        f"Win rate: {best.get('win_rate') if best.get('win_rate') is not None else '-'}%",
        f"Max drawdown: {best.get('max_drawdown_pct') if best.get('max_drawdown_pct') is not None else '-'}%",
        f"Profit factor: {best.get('profit_factor') if best.get('profit_factor') is not None else '-'}",
        f"Trades: {best.get('trades_count') or 0}",
        f"Latest signal: {best.get('latest_signal') or '-'}",
        "",
        "Frames",
    ]
    for row in rows[:4]:
        lines.append(
            f"- {row['timeframe']}: {row.get('recommendation')} | score {row.get('score') or 0:.0f}% | "
            f"return {row.get('total_return_pct') if row.get('total_return_pct') is not None else '-'}% | "
            f"WR {row.get('win_rate') if row.get('win_rate') is not None else '-'}% | DD {row.get('max_drawdown_pct') if row.get('max_drawdown_pct') is not None else '-'}%"
        )
    lines.extend(
        [
            "",
            "Risk note: Backtest entries use next-candle open and include commission/slippage, but future performance can differ.",
            f"Disclaimer: {DISCLAIMER}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reviewed EGX strategy backtests.")
    parser.add_argument("--symbol", type=str, default=None, help="Run one symbol, for example COMI.")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeframes", type=str, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    frames = [item.strip() for item in args.timeframes.split(",") if item.strip()] if args.timeframes else None
    with SessionLocal() as db:
        if args.symbol:
            result = run_symbol_backtest(args.symbol, timeframes=frames, db=db)
            print(format_backtest_report(db, result["symbol"]))
        else:
            symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
            result = run_universe_backtests(db=db, limit=args.limit, symbols=symbols)
            print(f"Backtested {len(result['rows'])} symbols.")
            for row in result["rows"][:10]:
                print(
                    f"{row['symbol']} {row.get('best_timeframe')} {row.get('recommendation')} "
                    f"score={row.get('score') or 0:.0f} return={row.get('total_return_pct')}"
                )


if __name__ == "__main__":
    main()
