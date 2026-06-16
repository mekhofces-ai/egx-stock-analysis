from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DISCLAIMER, Settings, get_settings
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import TradingViewScreeningResult, TradingViewScreeningRun
from app.services.screener_recommendations import build_final_recommendations


logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _latest_run(db: Session) -> TradingViewScreeningRun | None:
    return db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))


def run_tradingview_screening(
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int = 500,
    retries: int = 2,
) -> dict[str, Any]:
    """Persist a real TradingView/Telegram recommendation snapshot.

    This wraps the existing recommendation engine instead of fabricating rows. If TradingView is unavailable,
    the persisted run records the provider warning and any fallback rows the existing engine could build.
    """
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        started = datetime.utcnow()
        last_error: Exception | None = None
        rec_run = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                logger.info("TradingView screening started, attempt %s/%s.", attempt, retries)
                rec_run = build_final_recommendations(active_db, settings=settings, limit=limit)
                break
            except Exception as exc:
                last_error = exc
                logger.warning("TradingView screening attempt %s failed: %s", attempt, exc)
                if attempt < retries:
                    time.sleep(1.5 * attempt)

        if rec_run is None:
            with sqlite_write_lock():
                screening_run = TradingViewScreeningRun(
                    provider_status="unavailable",
                    provider_warning=str(last_error) if last_error else "TradingView screening failed.",
                    started_at=started,
                    completed_at=datetime.utcnow(),
                    symbols_count=0,
                )
                active_db.add(screening_run)
                active_db.commit()
            return {
                "run_id": screening_run.id,
                "provider_status": screening_run.provider_status,
                "provider_warning": screening_run.provider_warning,
                "symbols_count": 0,
                "rows": [],
            }

        with sqlite_write_lock():
            screening_run = TradingViewScreeningRun(provider_status=rec_run.provider_status, started_at=started, symbols_count=0)
            active_db.add(screening_run)
            active_db.flush()
            inserted = 0
            for row in rec_run.rows[:limit]:
                try:
                    symbol = str(row.get("symbol") or "").upper().replace("EGX:", "")
                    if not symbol:
                        continue
                    result = TradingViewScreeningResult(
                        run_id=screening_run.id,
                        symbol=symbol,
                        recommendation=row.get("final_recommendation"),
                        final_score=_num(row.get("final_score")),
                        tv_vote=row.get("tv_vote"),
                        telegram_vote=row.get("telegram_vote"),
                        close=_num(row.get("last_price")),
                        change_percent=_num(row.get("change_percent")),
                        rsi=_num(row.get("rsi")),
                        volume=_num(row.get("volume")),
                        technical_rating=_num(row.get("tv_recommend_all")),
                        moving_averages_rating=_num(row.get("tv_recommend_ma")),
                        oscillators_rating=_num(row.get("tv_recommend_other")),
                        raw=row,
                    )
                    active_db.add(result)
                    inserted += 1
                except Exception as exc:
                    logger.exception("Could not store TradingView screening row: %s", exc)
            screening_run.provider_warning = rec_run.provider_warning
            screening_run.symbols_count = inserted
            screening_run.completed_at = datetime.utcnow()
            active_db.commit()
        logger.info("TradingView screening completed. run_id=%s rows=%s status=%s", screening_run.id, inserted, rec_run.provider_status)
        return {
            "run_id": screening_run.id,
            "provider_status": rec_run.provider_status,
            "provider_warning": rec_run.provider_warning,
            "symbols_count": inserted,
            "rows": rec_run.rows[:limit],
        }

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def latest_screening_rows(db: Session, symbol: str | None = None, limit: int = 100) -> list[TradingViewScreeningResult]:
    run = _latest_run(db)
    if not run:
        return []
    stmt = select(TradingViewScreeningResult).where(TradingViewScreeningResult.run_id == run.id)
    if symbol:
        stmt = stmt.where(TradingViewScreeningResult.symbol == symbol.upper().replace("EGX:", ""))
    return db.scalars(stmt.order_by(TradingViewScreeningResult.final_score.desc()).limit(limit)).all()


def latest_screening_summary(db: Session, symbol: str | None = None) -> dict[str, Any]:
    run = _latest_run(db)
    if not run:
        return {"available": False, "message": "No TradingView screening run has been stored yet."}
    rows = latest_screening_rows(db, symbol=symbol, limit=200 if symbol is None else 1)
    return {
        "available": True,
        "run_id": run.id,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "provider_status": run.provider_status,
        "provider_warning": run.provider_warning,
        "symbols_count": run.symbols_count,
        "rows": [
            {
                "symbol": row.symbol,
                "recommendation": row.recommendation,
                "final_score": row.final_score,
                "tv_vote": row.tv_vote,
                "telegram_vote": row.telegram_vote,
                "close": row.close,
                "change_percent": row.change_percent,
                "rsi": row.rsi,
                "volume": row.volume,
                "technical_rating": row.technical_rating,
                "moving_averages_rating": row.moving_averages_rating,
                "oscillators_rating": row.oscillators_rating,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }


def format_screening_report(db: Session, limit: int = 5) -> str:
    summary = latest_screening_summary(db)
    if not summary["available"]:
        return f"{summary['message']}\nRun: python app/services/tradingview_screener.py\nDisclaimer: {DISCLAIMER}"

    rows = summary["rows"]
    bullish = [row for row in rows if str(row.get("recommendation") or "").upper() in {"BUY", "WATCH"}][:limit]
    bearish = [row for row in rows if str(row.get("recommendation") or "").upper() in {"SELL", "AVOID", "HIGH_RISK"}][:limit]
    strong = [row for row in rows if (row.get("technical_rating") or 0) >= 0.3][:limit]
    created_at = summary.get("completed_at") or summary.get("created_at")

    lines = [
        "Latest TradingView EGX Screening",
        f"Run: {created_at:%Y-%m-%d %H:%M} UTC" if created_at else "Run: -",
        f"Status: {summary['provider_status']} | screened {summary['symbols_count']} stocks",
        "",
        "Top bullish",
    ]
    if bullish:
        lines.extend(
            f"- {row['symbol']}: {row.get('recommendation')} | score {row.get('final_score') or 0:.0f}% | TV {row.get('tv_vote') or '-'} | RSI {row.get('rsi') or '-'}"
            for row in bullish
        )
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Top bearish")
    if bearish:
        lines.extend(
            f"- {row['symbol']}: {row.get('recommendation')} | score {row.get('final_score') or 0:.0f}% | TV {row.get('tv_vote') or '-'}"
            for row in bearish
        )
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Strong technical rating")
    if strong:
        lines.extend(
            f"- {row['symbol']}: technical {row.get('technical_rating') or 0:.2f} | MA {row.get('moving_averages_rating') or 0:.2f}"
            for row in strong
        )
    else:
        lines.append("- None")

    if summary.get("provider_warning"):
        lines.extend(["", f"Warning: {summary['provider_warning']}"])
    lines.extend(["", f"Disclaimer: {DISCLAIMER}"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and store the EGX TradingView screening snapshot.")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    result = run_tradingview_screening(limit=args.limit)
    print(
        f"TradingView screening run {result['run_id']} completed: "
        f"{result['symbols_count']} rows, status={result['provider_status']}."
    )
    if result.get("provider_warning"):
        print(f"Warning: {result['provider_warning']}")


if __name__ == "__main__":
    main()
