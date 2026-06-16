from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE, get_settings
from app.data.egx_symbols import list_active_symbols
from app.database import SessionLocal, init_db
from app.intelligence.final_decision_engine import build_final_decision
from app.intelligence.portfolio_bot import run_daily_portfolio_bot
from app.models import AutomationState
from app.services.dynamic_settings import get_bool, get_int, get_setting, seed_dynamic_settings, set_setting
from app.services.market_daily_evaluation import evaluate_daily_market
from app.services.opportunity_engine import refresh_opportunities
from app.services.tradingview_screener import run_tradingview_screening


logger = logging.getLogger(__name__)
CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)


def cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def _state_get(db: Session, key: str) -> str | None:
    row = db.get(AutomationState, key)
    return row.value if row else None


def _state_set(db: Session, key: str, value: str) -> None:
    row = db.get(AutomationState, key)
    now = datetime.utcnow()
    if row:
        row.value = value
        row.updated_at = now
    else:
        db.add(AutomationState(key=key, value=value, updated_at=now))


def daily_dynamic_refresh_due(db: Session, *, now: datetime | None = None, force: bool = False) -> bool:
    if force:
        return True
    seed_dynamic_settings(db)
    if not get_bool(db, "daily_dynamic_refresh_enabled", True):
        return False
    now = now or cairo_now()
    time_text = str(get_setting(db, "daily_dynamic_refresh_time", "08:30", "string") or "08:30").strip()
    try:
        hour_text, minute_text = time_text.split(":", 1)
        due_time = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
    except Exception:
        due_time = now.replace(hour=8, minute=30, second=0, microsecond=0)
    today_key = now.date().isoformat()
    return now >= due_time and _state_get(db, "last_daily_dynamic_refresh_date") != today_key


def run_daily_dynamic_refresh(
    db: Session | None = None,
    *,
    force: bool = False,
    limit: int | None = None,
    run_screening: bool = True,
    run_portfolio: bool | None = None,
    execute_portfolio: bool | None = None,
) -> dict[str, Any]:
    settings = get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        seed_dynamic_settings(active_db)
        now = cairo_now()
        if not daily_dynamic_refresh_due(active_db, now=now, force=force):
            return {
                "status": "skipped",
                "reason": "daily dynamic refresh already ran today or is not due yet",
                "date": now.date().isoformat(),
            }
        symbol_limit = limit or get_int(active_db, "daily_dynamic_refresh_symbol_limit", 250, minimum=1)
        symbols = list_active_symbols(active_db, limit=symbol_limit)
        result: dict[str, Any] = {
            "status": "running",
            "date": now.date().isoformat(),
            "symbol_limit": symbol_limit,
            "symbols_requested": len(symbols),
            "errors": [],
        }
        if run_screening:
            try:
                screening = run_tradingview_screening(active_db, settings=settings, limit=max(symbol_limit, 250))
                result["screening"] = {
                    "rows": len(screening.get("rows") or []),
                    "provider_status": screening.get("provider_status"),
                }
            except Exception as exc:
                logger.exception("Daily dynamic TradingView screening failed.")
                result["errors"].append(f"screening: {exc}")
        try:
            result["market_evaluation"] = evaluate_daily_market(active_db, persist=True)
        except Exception as exc:
            logger.exception("Daily dynamic market evaluation failed.")
            result["errors"].append(f"market_evaluation: {exc}")

        processed = 0
        for symbol in symbols:
            try:
                build_final_decision(active_db, symbol, run_sources=False, persist=True)
                processed += 1
                if processed % 25 == 0:
                    active_db.commit()
            except Exception as exc:
                logger.exception("Daily dynamic final decision failed for %s.", symbol)
                result["errors"].append(f"{symbol}: {exc}")
        active_db.commit()
        result["final_decisions_processed"] = processed

        try:
            opportunities = refresh_opportunities(active_db, settings=settings, limit=max(30, symbol_limit), run_screening=False)
            result["opportunities"] = {
                "saved": opportunities.get("saved"),
                "provider_status": opportunities.get("provider_status"),
            }
        except Exception as exc:
            logger.exception("Daily dynamic opportunities refresh failed.")
            result["errors"].append(f"opportunities: {exc}")

        portfolio_enabled = get_bool(active_db, "automation_run_portfolio_bot", False) if run_portfolio is None else bool(run_portfolio)
        if portfolio_enabled:
            try:
                execute = get_bool(active_db, "portfolio_bot_auto_execute_paper_trades", False) if execute_portfolio is None else bool(execute_portfolio)
                portfolio = run_daily_portfolio_bot(active_db, execute=execute, force=False, limit=get_int(active_db, "portfolio_bot_symbol_limit", 50, minimum=1))
                result["portfolio"] = {
                    "execute": execute,
                    "actions": len(portfolio.get("actions") or []),
                    "portfolio": portfolio.get("portfolio"),
                }
            except Exception as exc:
                logger.exception("Daily dynamic portfolio scan failed.")
                result["errors"].append(f"portfolio: {exc}")
        _state_set(active_db, "last_daily_dynamic_refresh_date", now.date().isoformat())
        _state_set(active_db, "last_daily_dynamic_refresh_at", now.isoformat(timespec="seconds"))
        _state_set(active_db, "last_daily_dynamic_refresh_errors", json.dumps(result["errors"][:10], ensure_ascii=False))
        result["status"] = "partial_success" if result["errors"] else "success"
        active_db.commit()
        return result

    if db is not None:
        return _run(db)
    with SessionLocal() as active_db:
        return _run(active_db)


def configure_daily_dynamic_defaults(db: Session, *, full_limit: int = 250, fast_limit: int = 25) -> None:
    seed_dynamic_settings(db)
    set_setting(db, "daily_dynamic_refresh_enabled", "true", value_type="bool")
    set_setting(db, "daily_dynamic_refresh_time", "08:30", value_type="string")
    set_setting(db, "daily_dynamic_refresh_symbol_limit", str(full_limit), value_type="int")
    set_setting(db, "automation_symbol_limit", str(fast_limit), value_type="int")
    set_setting(db, "dynamic_data_symbol_limit", "15", value_type="int")
    set_setting(db, "dynamic_data_timeframes", "1d", value_type="string")
    set_setting(db, "automation_interval_seconds", "600", value_type="int")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run daily dynamic EGX refresh.")
    parser.add_argument("--force", action="store_true", help="Run even if today's refresh already ran.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum active symbols to rebuild.")
    parser.add_argument("--no-screening", action="store_true", help="Skip TradingView screening.")
    parser.add_argument("--portfolio", action="store_true", help="Run portfolio scan as part of the refresh.")
    parser.add_argument("--execute-paper", action="store_true", help="Allow paper portfolio execution if portfolio scan is enabled.")
    parser.add_argument("--configure-defaults", action="store_true", help="Set practical daily/full and fast-cycle defaults.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    with SessionLocal() as db:
        if args.configure_defaults:
            configure_daily_dynamic_defaults(db)
            db.commit()
        result = run_daily_dynamic_refresh(
            db,
            force=args.force,
            limit=args.limit,
            run_screening=not args.no_screening,
            run_portfolio=args.portfolio,
            execute_portfolio=args.execute_paper,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
