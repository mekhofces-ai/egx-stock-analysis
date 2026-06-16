from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from filelock import FileLock, Timeout
from sqlalchemy import func, select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings, get_settings
from app.data.egx_symbols import list_active_symbols
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.intelligence.accuracy_tracker import update_signal_accuracy
from app.intelligence.final_decision_engine import build_final_decision
from app.intelligence.learning_engine import update_dynamic_weights
from app.intelligence.portfolio_bot import run_daily_portfolio_bot
from app.models import AutomationRun, AutomationState, BacktestQueue, Opportunity
from app.services.backtest_queue import process_backtest_queue
from app.services.dynamic_settings import automation_snapshot, get_bool, get_int, seed_dynamic_settings, set_setting
from app.services.daily_dynamic_refresh import daily_dynamic_refresh_due, run_daily_dynamic_refresh
from app.services.trading_safety import disable_trading_for_audit, safety_snapshot
from app.services.dynamic_data_refresh import run_dynamic_data_refresh
from app.services.ai_llm_service import run_ai_analysis
from app.services.ingestion import run_ingestion_cycle
from app.services.opportunity_engine import refresh_opportunities, send_buy_alerts, send_strategy_notifications
from app.services.scheduler import run_due_reports_once
from app.services.stock_analysis_engine import refresh_combined_analysis
from app.services.strategy_registry import CLI_V6_CODE, LEGACY_CODE, ensure_strategy_registry, get_strategy, run_strategy
from app.services.trading_safety import execution_block_reason
from app.services.tradingview_screener import run_tradingview_screening
from app.telegram.telegram_analyzer import process_recent_messages


DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "automation_safe.log"
LOCK_FILE = DATA_DIR / "automation_cycle.lock"

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("egx_automation")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _state_set(db: Session, key: str, value: Any) -> None:
    row = db.get(AutomationState, key)
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
    if row:
        row.value = text
        row.updated_at = utcnow()
    else:
        db.add(AutomationState(key=key, value=text, updated_at=utcnow()))


def _state_get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(AutomationState, key)
    if not row or row.value is None:
        return default
    text = row.value
    try:
        return json.loads(text)
    except Exception:
        return text


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _cycle_lock_is_held() -> bool:
    lock = FileLock(str(LOCK_FILE), timeout=0)
    try:
        lock.acquire(timeout=0)
        lock.release()
        return False
    except Timeout:
        return True


def get_automation_status(db: Session | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        seed_dynamic_settings(active_db)
        snapshot = automation_snapshot(active_db, settings=settings)
        latest = active_db.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
        safety = safety_snapshot(active_db)
        mode = "audit" if safety.get("audit_mode") else "live" if safety.get("live_trading_enabled") else "paper"
        stale_running = False
        running = False
        if latest and latest.status == "running" and latest.finished_at is None:
            age_seconds = (utcnow() - latest.started_at).total_seconds()
            stale_running = (not _cycle_lock_is_held()) or age_seconds > max(600, int(snapshot["interval_seconds"]) * 3)
            running = not stale_running
        last_finished = latest.finished_at if latest else None
        next_run = None
        if last_finished:
            next_run = last_finished + timedelta(seconds=int(snapshot["interval_seconds"]))
        return {
            "enabled": snapshot["enabled"],
            "running": running,
            "interval_seconds": snapshot["interval_seconds"],
            "last_run_time": latest.started_at.isoformat(sep=" ", timespec="seconds") if latest else None,
            "last_finished_at": last_finished.isoformat(sep=" ", timespec="seconds") if last_finished else None,
            "next_run_time": next_run.isoformat(sep=" ", timespec="seconds") if next_run else None,
            "last_status": "stale_running" if stale_running else latest.status if latest else _state_get(active_db, "status", "never_run"),
            "last_error": ("Previous running cycle is stale." if stale_running else latest.error_message) if latest else _state_get(active_db, "last_error"),
            "last_alert_count": latest.alerts_sent if latest else 0,
            "current_mode": mode,
            "execution_blocked": safety.get("execution_blocked"),
            "live_trading_enabled": safety.get("live_trading_enabled"),
            "emergency_stop_enabled": safety.get("emergency_stop_enabled"),
            "symbols_processed": latest.symbols_processed if latest else 0,
            "opportunities_count": latest.opportunities_count if latest else 0,
            "telegram_fetch_status": latest.telegram_fetch_status if latest else None,
            "strategy_status": latest.strategy_status if latest else None,
            "backtest_status": latest.backtest_status if latest else None,
            "opportunity_status": latest.opportunity_status if latest else None,
            "settings": snapshot,
        }

    if db is not None:
        return _run(db)
    with SessionLocal() as active_db:
        return _run(active_db)


def set_automation_enabled(enabled: bool, *args, **kwargs) -> bool:  # noqa: ANN002, ANN003
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        set_setting(db, "automation_enabled", "true" if enabled else "false", value_type="bool")
        _state_set(db, "status", "enabled" if enabled else "disabled")
        db.commit()
    return True


def _task_result(ok: bool, label: str, detail: Any = None) -> dict[str, Any]:
    return {"ok": bool(ok), "status": "ok" if ok else "failed", "label": label, "detail": detail}


def _apply_media_env(snapshot: dict[str, Any]) -> None:
    os.environ["TELEGRAM_DOWNLOAD_MEDIA"] = "true" if snapshot.get("telegram_download_media") else "false"
    os.environ["TELEGRAM_ANALYZE_IMAGES"] = "true" if snapshot.get("telegram_analyze_images") else "false"
    os.environ["TELEGRAM_SKIP_NON_IMAGE_MEDIA"] = "true"
    os.environ["TELEGRAM_FETCH_LIMIT_PER_CHANNEL"] = str(snapshot.get("telegram_fetch_limit") or 30)


def _run_telegram_fetch(db: Session, settings: Settings, snapshot: dict[str, Any], *, send_alerts: bool = True) -> dict[str, Any]:
    _apply_media_env(snapshot)
    result = run_ingestion_cycle(
        limit=int(snapshot.get("telegram_fetch_limit") or settings.telegram_fetch_limit_per_channel),
        send_alerts=send_alerts,
    )
    classified = process_recent_messages(db, limit=int(snapshot.get("telegram_fetch_limit") or settings.telegram_fetch_limit_per_channel) * 3)
    db.commit()
    return _task_result(True, "telegram_fetch", {"ingestion": result.to_dict(), "classified": classified})


def _run_tradingview(db: Session, settings: Settings) -> dict[str, Any]:
    result = run_tradingview_screening(db, settings=settings, limit=500)
    return _task_result(True, "tradingview", {"rows": len(result.get("rows") or []), "provider_status": result.get("provider_status")})


def _seconds_since_state(db: Session, key: str) -> float | None:
    value = _state_get(db, key)
    dt = _parse_dt(value)
    if not dt:
        return None
    return (utcnow() - dt).total_seconds()


def _due_by_state(db: Session, key: str, interval_seconds: int, *, force: bool = False) -> bool:
    if force:
        return True
    elapsed = _seconds_since_state(db, key)
    return elapsed is None or elapsed >= interval_seconds


def _run_dynamic_sources(db: Session, settings: Settings, snapshot: dict[str, Any]) -> dict[str, Any]:
    financial_due = bool(snapshot.get("fetch_financial_data")) and _due_by_state(
        db,
        "last_financial_data_refresh_at",
        int(snapshot.get("financial_refresh_interval_seconds") or 86400),
    )
    news_due = bool(snapshot.get("fetch_news_data")) and _due_by_state(
        db,
        "last_news_data_refresh_at",
        int(snapshot.get("news_refresh_interval_seconds") or 3600),
    )
    ohlcv_due = bool(snapshot.get("fetch_ohlcv_data")) and _due_by_state(
        db,
        "last_ohlcv_data_refresh_at",
        int(snapshot.get("ohlcv_refresh_interval_seconds") or 600),
    )
    if not any([financial_due, news_due, ohlcv_due]):
        return _task_result(
            True,
            "dynamic_data",
            {"skipped": True, "reason": "refresh intervals not due"},
        )
    result = run_dynamic_data_refresh(
        db,
        settings=settings,
        limit=int(snapshot.get("dynamic_data_symbol_limit") or 20),
        refresh_financial=financial_due,
        refresh_news=news_due,
        refresh_ohlcv=ohlcv_due,
    )
    now = now_iso()
    if financial_due:
        _state_set(db, "last_financial_data_refresh_at", now)
    if news_due:
        _state_set(db, "last_news_data_refresh_at", now)
    if ohlcv_due:
        _state_set(db, "last_ohlcv_data_refresh_at", now)
    db.commit()
    failed = []
    for key, value in result.items():
        errors = value.get("errors") if isinstance(value, dict) else None
        if errors:
            failed.append(f"{key}: {len(errors)} error(s)")
    return _task_result(not failed, "dynamic_data", {"result": result, "warnings": failed})


def _run_strategies(db: Session, settings: Settings, snapshot: dict[str, Any]) -> dict[str, Any]:
    ensure_strategy_registry(db)
    limit = int(snapshot.get("automation_symbol_limit") or settings.automation_symbol_limit or settings.strategy_symbol_limit)
    if limit <= 0:
        limit = settings.strategy_symbol_limit
    rows = []
    errors: list[str] = []
    for code, enabled_key in [(LEGACY_CODE, "run_strategy_legacy"), (CLI_V6_CODE, "run_cli_v6")]:
        if not snapshot.get(enabled_key):
            continue
        strategy = get_strategy(db, code)
        if strategy and not strategy.is_enabled:
            continue
        try:
            partial = run_strategy(code, db=db, settings=settings, limit=limit)
            rows.extend(partial.get("rows") or [])
            errors.extend(partial.get("errors") or [])
        except Exception as exc:
            errors.append(f"{code}: {exc}")
    return _task_result(not errors, "strategies", {"rows": len(rows), "errors": errors[:8]})


def _backtest_due(db: Session, mode: str, force: bool) -> bool:
    if force:
        return True
    mode = (mode or "opportunities_only").lower()
    if mode == "manual_only":
        return False
    last_text = _state_get(db, "last_backtest_at")
    last = _parse_dt(last_text)
    if last is None:
        return True
    if mode == "daily":
        return last.date() < utcnow().date()
    if mode == "hourly":
        return utcnow() - last >= timedelta(hours=1)
    if mode == "opportunities_only":
        count = db.scalar(select(func.count()).select_from(BacktestQueue).where(BacktestQueue.status == "pending"))
        return int(count or 0) > 0 and utcnow() - last >= timedelta(minutes=30)
    return False


def _run_backtests(db: Session, snapshot: dict[str, Any], force: bool = False) -> dict[str, Any]:
    mode = str(snapshot.get("backtest_mode") or "opportunities_only")
    if not _backtest_due(db, mode, force):
        return _task_result(True, "backtests", {"skipped": True, "mode": mode})
    result = process_backtest_queue(db, limit=int(snapshot.get("backtest_queue_limit") or 10), timeframes=["1d"])
    _state_set(db, "last_backtest_at", now_iso())
    db.commit()
    return _task_result(result.get("failed", 0) == 0, "backtests", result)


def _run_combined(db: Session, settings: Settings, snapshot: dict[str, Any]) -> dict[str, Any]:
    limit = int(snapshot.get("automation_symbol_limit") or settings.automation_symbol_limit or settings.strategy_symbol_limit)
    symbols = [
        row.symbol
        for row in db.scalars(select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(limit)).all()
    ]
    result = refresh_combined_analysis(db, symbols=symbols or None, settings=settings, limit=limit, run_missing=False)
    return _task_result(len(result.get("errors") or []) == 0, "combined_analysis", result)


def _run_final_decisions(db: Session, settings: Settings, snapshot: dict[str, Any]) -> dict[str, Any]:
    limit = int(snapshot.get("automation_symbol_limit") or settings.automation_symbol_limit or settings.strategy_symbol_limit)
    symbols = [
        row.symbol
        for row in db.scalars(select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(limit)).all()
    ]
    if not symbols:
        symbols = list_active_symbols(db, limit=limit)
    processed = 0
    errors: list[str] = []
    for symbol in symbols:
        try:
            build_final_decision(db, symbol, run_sources=False, persist=True)
            processed += 1
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    db.commit()
    return _task_result(not errors, "final_decisions", {"processed": processed, "errors": errors[:8]})


def _run_accuracy_learning(db: Session) -> dict[str, Any]:
    accuracy = update_signal_accuracy(db, limit=300)
    learning = update_dynamic_weights(db)
    db.commit()
    return _task_result(True, "accuracy_learning", {"accuracy": accuracy, "learning": learning})


def _run_portfolio(db: Session, snapshot: dict[str, Any]) -> dict[str, Any]:
    block_reason = execution_block_reason(db, block_paper_execution=False)
    if block_reason:
        return _task_result(
            True,
            "portfolio_bot",
            {
                "skipped": True,
                "execute": False,
                "actions": 0,
                "transactions": 0,
                "pending_approvals": 0,
                "reason": block_reason,
            },
        )
    execute = bool(snapshot.get("portfolio_auto_execute"))
    limit = int(snapshot.get("portfolio_symbol_limit") or 50)
    result = run_daily_portfolio_bot(db, execute=execute, force=False, limit=limit)
    db.commit()
    actions = result.get("actions") or []
    transactions = [row for row in actions if row.get("status") in {"bought", "sold"}]
    approvals = [row for row in actions if row.get("status") == "pending_approval"]
    return _task_result(
        True,
        "portfolio_bot",
        {
            "execute": execute,
            "actions": len(actions),
            "transactions": len(transactions),
            "pending_approvals": len(approvals),
            "portfolio": result.get("portfolio"),
        },
    )


def _run_opportunities(db: Session, settings: Settings, run_screening: bool) -> dict[str, Any]:
    result = refresh_opportunities(db, settings=settings, limit=max(settings.strategy_symbol_limit, 30), run_screening=run_screening)
    return _task_result(True, "opportunities", {"saved": result.get("saved"), "provider_status": result.get("provider_status")})


def _run_alerts(db: Session, settings: Settings, no_alerts: bool = False) -> dict[str, Any]:
    if no_alerts:
        return _task_result(True, "alerts", {"skipped": True})
    strategy = send_strategy_notifications(db, settings=settings)
    opportunities = send_buy_alerts(db, settings=settings)
    sent = int(strategy.get("sent") or 0) + int(opportunities.get("sent") or 0)
    return _task_result(True, "alerts", {"sent": sent, "strategy": strategy, "opportunities": opportunities})


def _run_daily_reports_due() -> dict[str, Any]:
    results = run_due_reports_once()
    sent = sum(1 for row in results if row.get("sent"))
    skipped = sum(1 for row in results if row.get("skipped_duplicate"))
    return _task_result(True, "daily_reports", {"sent": sent, "skipped_duplicate": skipped, "results": results})


def _run_daily_dynamic_refresh_due(db: Session) -> dict[str, Any]:
    if not daily_dynamic_refresh_due(db):
        return _task_result(True, "daily_dynamic_refresh", {"skipped": True, "reason": "not due"})
    result = run_daily_dynamic_refresh(db, force=False, run_portfolio=True)
    return _task_result(result.get("status") in {"success", "partial_success", "skipped"}, "daily_dynamic_refresh", result)


def _run_ai_analysis() -> dict[str, Any]:
    result = run_ai_analysis()
    status = result.get("status", "failed")
    return _task_result(
        status == "completed",
        "ai_analysis",
        {
            "symbols_analyzed": result.get("symbols_analyzed", 0),
            "errors": result.get("errors", 0),
            "tokens_used": result.get("total_tokens_used", 0),
            "run_id": result.get("run_id"),
        },
    )


def _mark_run(
    db: Session,
    run: AutomationRun,
    *,
    status: str,
    started_at: datetime,
    task_results: dict[str, Any],
    errors: list[str],
) -> None:
    run.finished_at = utcnow()
    run.duration_seconds = round((run.finished_at - started_at).total_seconds(), 2)
    run.status = status
    run.telegram_fetch_status = task_results.get("telegram_fetch", {}).get("status")
    run.strategy_status = task_results.get("strategies", {}).get("status")
    run.backtest_status = task_results.get("backtests", {}).get("status")
    run.opportunity_status = task_results.get("opportunities", {}).get("status")
    strategy_detail = task_results.get("strategies", {}).get("detail") or {}
    opportunity_detail = task_results.get("opportunities", {}).get("detail") or {}
    alert_detail = task_results.get("alerts", {}).get("detail") or {}
    portfolio_detail = task_results.get("portfolio_bot", {}).get("detail") or {}
    run.symbols_processed = int(strategy_detail.get("rows") or 0)
    run.opportunities_count = int(opportunity_detail.get("saved") or 0)
    run.alerts_sent = int(alert_detail.get("sent") or 0) + int(portfolio_detail.get("transactions") or 0)
    run.error_message = "; ".join(errors[:8]) if errors else None
    _state_set(db, "status", status)
    _state_set(db, "running", "false")
    _state_set(db, "last_run_id", run.run_id)
    _state_set(db, "last_finished_at", run.finished_at.isoformat())
    _state_set(db, "last_alert_count", run.alerts_sent)
    if errors:
        _state_set(db, "last_error", run.error_message)
    else:
        _state_set(db, "last_error", "")
    db.commit()


def _run_cycle_locked(
    settings: Settings | None = None,
    *,
    force_backtest: bool = False,
    no_alerts: bool = False,
    skip_backtests: bool = False,
    task_filter: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    run_id = f"auto_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    started_at = utcnow()
    task_results: dict[str, Any] = {}
    errors: list[str] = []
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        snapshot = automation_snapshot(db, settings=settings)
        run = AutomationRun(run_id=run_id, started_at=started_at, status="running", alerts_sent=0)
        db.add(run)
        _state_set(db, "status", "running")
        _state_set(db, "current_run_id", run_id)
        db.commit()
        logger.info("Automation cycle started: %s", run_id)

        def maybe_run(name: str, enabled: bool, fn) -> None:  # noqa: ANN001
            if task_filter and task_filter != name:
                return
            if not enabled:
                task_results[name] = _task_result(True, name, {"skipped": True, "disabled": True})
                return
            try:
                task_results[name] = fn()
            except Exception as exc:
                logger.exception("Automation task failed: %s", name)
                task_results[name] = _task_result(False, name, {"error": str(exc)})
                errors.append(f"{name}: {exc}")

        maybe_run("telegram_fetch", bool(snapshot.get("fetch_telegram")), lambda: _run_telegram_fetch(db, settings, snapshot, send_alerts=not no_alerts))
        maybe_run("tradingview", bool(snapshot.get("run_tradingview")), lambda: _run_tradingview(db, settings))
        maybe_run(
            "dynamic_data",
            bool(snapshot.get("fetch_financial_data") or snapshot.get("fetch_news_data") or snapshot.get("fetch_ohlcv_data")),
            lambda: _run_dynamic_sources(db, settings, snapshot),
        )
        maybe_run("strategies", bool(snapshot.get("run_strategy_legacy") or snapshot.get("run_cli_v6")), lambda: _run_strategies(db, settings, snapshot))
        maybe_run("backtests", not skip_backtests, lambda: _run_backtests(db, snapshot, force=force_backtest))
        maybe_run("combined_analysis", True, lambda: _run_combined(db, settings, snapshot))
        maybe_run("ai_analysis", bool(snapshot.get("run_ai_analysis")), lambda: _run_ai_analysis())
        maybe_run("final_decisions", bool(snapshot.get("run_final_decisions")), lambda: _run_final_decisions(db, settings, snapshot))
        maybe_run("accuracy_learning", bool(snapshot.get("update_accuracy")), lambda: _run_accuracy_learning(db))
        maybe_run("opportunities", bool(snapshot.get("run_opportunities")), lambda: _run_opportunities(db, settings, run_screening=not bool(snapshot.get("run_tradingview"))))
        maybe_run("portfolio_bot", bool(snapshot.get("run_portfolio_bot")), lambda: _run_portfolio(db, snapshot))
        maybe_run("daily_dynamic_refresh", True, lambda: _run_daily_dynamic_refresh_due(db))
        maybe_run("daily_reports", True, _run_daily_reports_due)
        maybe_run("alerts", bool(snapshot.get("send_alerts")), lambda: _run_alerts(db, settings, no_alerts=no_alerts))

        status = "success" if not errors else "partial_success" if len(errors) < len(task_results) else "failed"
        _mark_run(db, run, status=status, started_at=started_at, task_results=task_results, errors=errors)
        logger.info("Automation cycle finished: %s | %s", run_id, status)
        return {
            "run_id": run_id,
            "status": status,
            "started_at": started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_seconds": run.duration_seconds,
            "symbols_processed": run.symbols_processed,
            "opportunities_count": run.opportunities_count,
            "alerts_sent": run.alerts_sent,
            "tasks": task_results,
            "errors": errors,
        }


def run_automation_cycle(
    settings: Settings | None = None,
    *,
    force_backtest: bool = False,
    no_alerts: bool = False,
    skip_backtests: bool = False,
    task_filter: str | None = None,
) -> dict[str, Any]:
    lock = FileLock(str(LOCK_FILE), timeout=2)
    try:
        with lock:
            return _run_cycle_locked(
                settings=settings,
                force_backtest=force_backtest,
                no_alerts=no_alerts,
                skip_backtests=skip_backtests,
                task_filter=task_filter,
            )
    except Timeout:
        logger.warning("Automation cycle skipped: previous cycle is still running.")
        with SessionLocal() as db:
            _state_set(db, "status", "skipped_locked")
            _state_set(db, "last_error", "previous cycle still running")
            db.commit()
        return {"status": "skipped_locked", "message": "previous cycle still running"}


def safe_cycle(force_backtest: bool = False) -> dict[str, Any]:
    return run_automation_cycle(force_backtest=force_backtest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EGX automation.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--force-backtest", action="store_true", help="Process the backtest queue now.")
    parser.add_argument("--no-alerts", action="store_true", help="Do not send Telegram alerts in this run.")
    parser.add_argument("--skip-backtests", action="store_true", help="Skip backtest queue processing.")
    parser.add_argument("--telegram-only", action="store_true", help="Run Telegram fetch only.")
    parser.add_argument("--strategies-only", action="store_true", help="Run strategies only.")
    parser.add_argument("--opportunities-only", action="store_true", help="Run opportunities only.")
    parser.add_argument("--alerts-only", action="store_true", help="Send alerts only.")
    parser.add_argument("--dynamic-data-only", action="store_true", help="Refresh due dynamic financial/news/OHLCV data only.")
    parser.add_argument("--final-decisions-only", action="store_true", help="Run final weighted decisions only.")
    parser.add_argument("--accuracy-only", action="store_true", help="Update accuracy and learning weights only.")
    parser.add_argument("--portfolio-only", action="store_true", help="Run paper portfolio scan only.")
    parser.add_argument("--daily-reports-only", action="store_true", help="Run due daily reports only.")
    parser.add_argument("--test", action="store_true", help="Initialize and print automation status.")
    parser.add_argument("--audit-mode", action="store_true", help="Force audit/paper safety before running.")
    parser.add_argument("--audit-continuous", action="store_true", help="Run continuously with audit safety enforced and live trading disabled.")
    parser.add_argument("--paper-alerts", action="store_true", help="Allow Telegram alerts while keeping live trading blocked.")
    parser.add_argument("--stop", action="store_true", help="Disable continuous automation safely and exit.")
    args = parser.parse_args()

    init_db(seed=True)
    settings = get_settings()
    if args.audit_mode or args.audit_continuous:
        with SessionLocal() as db:
            disable_trading_for_audit(db)
            if args.audit_continuous:
                set_setting(db, "automation_enabled", "true", value_type="bool")
                set_setting(db, "automation_send_alerts", "true" if args.paper_alerts else "false", value_type="bool")
            db.commit()
    if args.stop:
        with SessionLocal() as db:
            set_setting(db, "automation_enabled", "false", value_type="bool")
            _state_set(db, "status", "stopped")
            _state_set(db, "running", "false")
            db.commit()
        print(json.dumps({"status": "stopped", "message": "Automation disabled safely. Existing cycle locks are not killed."}, indent=2))
        return
    with SessionLocal() as db:
        snapshot = automation_snapshot(db, settings=settings)
    if args.test:
        with SessionLocal() as db:
            print(json.dumps(get_automation_status(db, settings=settings), ensure_ascii=True, indent=2, default=str))
        return

    task_filter = None
    if args.telegram_only:
        task_filter = "telegram_fetch"
    elif args.strategies_only:
        task_filter = "strategies"
    elif args.opportunities_only:
        task_filter = "opportunities"
    elif args.alerts_only:
        task_filter = "alerts"
    elif args.dynamic_data_only:
        task_filter = "dynamic_data"
    elif args.final_decisions_only:
        task_filter = "final_decisions"
    elif args.accuracy_only:
        task_filter = "accuracy_learning"
    elif args.portfolio_only:
        task_filter = "portfolio_bot"
    elif args.daily_reports_only:
        task_filter = "daily_reports"

    if args.once or task_filter:
        result = run_automation_cycle(
            settings=settings,
            force_backtest=args.force_backtest,
            no_alerts=args.no_alerts and not args.paper_alerts,
            skip_backtests=args.skip_backtests,
            task_filter=task_filter,
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
        return

    logger.info(
        "Automation runner started. Enabled=%s interval=%s audit_continuous=%s paper_alerts=%s",
        snapshot["enabled"],
        snapshot["interval_seconds"],
        args.audit_continuous,
        args.paper_alerts,
    )
    while True:
        with SessionLocal() as db:
            snapshot = automation_snapshot(db, settings=settings)
        if not snapshot["enabled"]:
            logger.info("Automation disabled. Sleeping %s seconds.", snapshot["interval_seconds"])
            time.sleep(int(snapshot["interval_seconds"]))
            continue
        run_automation_cycle(settings=settings)
        with SessionLocal() as db:
            interval = automation_snapshot(db, settings=settings)["interval_seconds"]
        logger.info("Sleeping %s seconds before next automation cycle.", interval)
        time.sleep(int(interval))


if __name__ == "__main__":
    main()
