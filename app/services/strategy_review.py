from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DISCLAIMER, Settings, get_settings
from app.database import SessionLocal, init_db
from app.models import StrategyBacktestSummary
from app.services.backtest_engine import STRATEGY_NAME, get_latest_backtest_summary, review_strategy_rules
from app.services.strategy import run_strategy_for_symbol


logger = logging.getLogger(__name__)


def _fmt(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def generate_strategy_health_report(db: Session | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        summaries = active_db.scalars(select(StrategyBacktestSummary)).all()
        rows = []
        for item in summaries:
            metrics = ((item.summary_json or {}).get("metrics") or {})
            rows.append(
                {
                    "symbol": item.symbol,
                    "timeframe": item.timeframe,
                    "score": item.score,
                    "recommendation": item.recommendation,
                    "total_return_pct": metrics.get("total_return_pct"),
                    "win_rate": metrics.get("win_rate"),
                    "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                    "profit_factor": metrics.get("profit_factor"),
                    "trades_count": metrics.get("trades_count"),
                    "updated_at": item.updated_at,
                }
            )
        tested_symbols = len({row["symbol"] for row in rows})
        available = [row for row in rows if row.get("recommendation") != "UNAVAILABLE"]
        avg_return = sum(float(row.get("total_return_pct") or 0) for row in available) / len(available) if available else None
        avg_win_rate = sum(float(row.get("win_rate") or 0) for row in available if row.get("win_rate") is not None)
        win_rate_count = sum(1 for row in available if row.get("win_rate") is not None)
        avg_drawdown = sum(float(row.get("max_drawdown_pct") or 0) for row in available) / len(available) if available else None
        avg_score = sum(float(row.get("score") or 0) for row in available) / len(available) if available else 0.0
        stability_score = max(0.0, min(100.0, avg_score - (avg_drawdown or 0) * 0.25))
        if not available:
            recommendation = "RUN_BACKTEST"
        elif stability_score >= 72 and (avg_return or 0) > 0:
            recommendation = "USE_FOR_CONFIRMED_SETUPS"
        elif stability_score >= 55:
            recommendation = "USE_AS_CONFIRMATION_ONLY"
        else:
            recommendation = "REVIEW_PARAMETERS"
        return {
            "strategy_name": STRATEGY_NAME,
            "generated_at": datetime.utcnow().isoformat(),
            "symbols_tested": tested_symbols,
            "frames_tested": len(rows),
            "average_return": round(avg_return, 2) if avg_return is not None else None,
            "win_rate": round(avg_win_rate / win_rate_count, 2) if win_rate_count else None,
            "drawdown": round(avg_drawdown, 2) if avg_drawdown is not None else None,
            "stability_score": round(stability_score, 2),
            "recommendation": recommendation,
            "rules": review_strategy_rules(),
            "top_rows": sorted(rows, key=lambda row: row.get("score") or 0, reverse=True)[:20],
            "disclaimer": DISCLAIMER,
        }

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def get_symbol_strategy_state(symbol: str, db: Session | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    symbol = symbol.upper().replace("EGX:", "").strip()

    def _run(active_db: Session) -> dict[str, Any]:
        try:
            state = run_strategy_for_symbol(active_db, symbol=symbol, settings=settings)
        except Exception as exc:
            logger.exception("Strategy state failed for %s", symbol)
            state = {
                "symbol": symbol,
                "strategy_action": "UNAVAILABLE",
                "strategy_score": 0.0,
                "data_quality": "unavailable",
                "timeframes": [],
                "error": str(exc),
            }
        backtests = get_latest_backtest_summary(active_db, symbol=symbol, limit=10)
        return {"symbol": symbol, "state": state, "backtests": backtests, "rules": review_strategy_rules(), "disclaimer": DISCLAIMER}

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def format_strategy_report(db: Session, symbol: str, settings: Settings | None = None) -> str:
    data = get_symbol_strategy_state(symbol, db=db, settings=settings)
    state = data["state"]
    symbol = data["symbol"]
    lines = [
        f"EGX Strategy State: {symbol}",
        f"Action: {state.get('strategy_action')} | score {_fmt(state.get('strategy_score'))}%",
        f"Data quality: {state.get('data_quality') or '-'} | reference {state.get('reference_provider') or '-'} {_fmt(state.get('reference_price'), 2)}",
        "",
        "Timeframes",
    ]
    frames = state.get("timeframes") or []
    if frames:
        for frame in frames[:4]:
            lines.append(
                f"- {frame.get('timeframe')}: {frame.get('action')} {_fmt(frame.get('score'))}% | "
                f"{frame.get('trend')} | return {_fmt(frame.get('total_return_pct'), 1)}% | "
                f"WR {_fmt(frame.get('win_rate'))}% | DD {_fmt(frame.get('max_drawdown_pct'), 1)}%"
            )
    else:
        lines.append("- No trusted candle data available.")

    backtests = data.get("backtests") or []
    lines.extend(["", "Stored reviewed backtest"])
    if backtests:
        for row in sorted(backtests, key=lambda item: item.get("score") or 0, reverse=True)[:4]:
            lines.append(
                f"- {row['timeframe']}: {row.get('recommendation')} | score {_fmt(row.get('score'))}% | "
                f"return {_fmt(row.get('total_return_pct'), 1)}% | PF {_fmt(row.get('profit_factor'), 2)}"
            )
    else:
        lines.append("- None yet. Run /backtest SYMBOL or python app/services/backtest_engine.py --symbol SYMBOL.")

    lines.extend(["", "Risk note: Use strategy only as confirmation with stop loss and position sizing.", f"Disclaimer: {DISCLAIMER}"])
    return "\n".join(lines)


def format_health_report(db: Session) -> str:
    health = generate_strategy_health_report(db=db)
    lines = [
        "EGX Strategy Health",
        f"Strategy: {health['strategy_name']}",
        f"Symbols tested: {health['symbols_tested']} | frames {health['frames_tested']}",
        f"Average return: {_fmt(health.get('average_return'), 1)}%",
        f"Win rate: {_fmt(health.get('win_rate'))}%",
        f"Drawdown: {_fmt(health.get('drawdown'), 1)}%",
        f"Stability: {_fmt(health.get('stability_score'))}%",
        f"Recommendation: {health.get('recommendation')}",
        f"Disclaimer: {DISCLAIMER}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review the EGX strategy state.")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    with SessionLocal() as db:
        if args.symbol:
            print(format_strategy_report(db, args.symbol))
        else:
            print(format_health_report(db))


if __name__ == "__main__":
    main()
