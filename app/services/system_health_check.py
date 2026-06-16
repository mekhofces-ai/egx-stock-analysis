from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from app.config import DAILY_FILE_REPORT_TIME, REPORT_TIMEZONE, get_settings
from app.database import SessionLocal, engine, init_db
from app.services.trading_safety import safety_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
REPORT_DIR = PROJECT_ROOT / "reports" / "daily"
logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        handler = logging.FileHandler(LOG_DIR / "system_health_check.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _row(component: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"component": component, "status": status, "message": message, "details": details or {}}


def _check_import(module_name: str) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
        return _row(module_name, "OK", "Import succeeded.")
    except Exception as exc:
        logger.exception("Import health check failed for %s", module_name)
        return _row(module_name, "ERROR", str(exc))


def run_health_check(*, save_log: bool = True) -> list[dict[str, Any]]:
    _setup_logging()
    rows: list[dict[str, Any]] = []

    try:
        settings = get_settings()
        rows.append(
            _row(
                "Config",
                "OK",
                "Settings loaded without exposing secrets.",
                {
                    "timezone": REPORT_TIMEZONE,
                    "daily_file_report_time": settings.daily_file_report_time or DAILY_FILE_REPORT_TIME,
                    "database_configured": bool(settings.database_url),
                    "telegram_token_configured": bool(settings.telegram_bot_token),
                    "allowed_chat_ids_configured": bool(settings.allowed_chat_ids),
                },
            )
        )
    except Exception as exc:
        logger.exception("Config health check failed.")
        rows.append(_row("Config", "ERROR", str(exc)))
        settings = None

    try:
        init_db(seed=True)
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar()
        rows.append(_row("Database", "OK", "SQLite connection and initialization succeeded."))
    except Exception as exc:
        logger.exception("Database health check failed.")
        rows.append(_row("Database", "ERROR", str(exc)))

    try:
        inspector = inspect(engine)
        required_tables = [
            "stocks",
            "recommendation_reports",
            "recommendation_items",
            "daily_loss_audit_reports",
            "market_daily_evaluations",
            "daily_file_reports",
            "strategy_backtest_summary",
            "telegram_messages",
            "notification_log",
            "live_trade_execution_logs",
            "repeated_recommendation_audit",
            "recommendation_evaluations",
            "end_of_day_review_reports",
            "end_of_day_review_items",
            "decision_snapshots",
            "walk_forward_runs",
            "walk_forward_periods",
            "intraday_scan_runs",
            "intraday_scan_items",
            "source_accuracy_snapshots",
            "pump_risk_snapshots",
            "risk_expectancy_snapshots",
            "recommendation_quality_snapshots",
            "strategy_learning_reports",
        ]
        missing = [table for table in required_tables if not inspector.has_table(table)]
        rows.append(
            _row(
                "Tables",
                "OK" if not missing else "ERROR",
                "All required tables exist." if not missing else f"Missing tables: {', '.join(missing)}",
                {"missing": missing},
            )
        )
    except Exception as exc:
        logger.exception("Table health check failed.")
        rows.append(_row("Tables", "ERROR", str(exc)))

    try:
        with SessionLocal() as db:
            snapshot = safety_snapshot(db)
        live_enabled = bool(snapshot.get("live_trading_enabled"))
        blocked = bool(snapshot.get("execution_blocked"))
        rows.append(
            _row(
                "Trading Safety",
                "OK" if not live_enabled and blocked else "WARNING",
                "Live trading is disabled and execution is blocked."
                if not live_enabled and blocked
                else "Review trading safety settings before any execution.",
                {
                    "audit_mode": snapshot.get("audit_mode"),
                    "emergency_stop_trading": snapshot.get("emergency_stop_trading"),
                    "live_trading_enabled": snapshot.get("live_trading_enabled"),
                    "execution_blocked": snapshot.get("execution_blocked"),
                },
            )
        )
    except Exception as exc:
        logger.exception("Trading safety health check failed.")
        rows.append(_row("Trading Safety", "ERROR", str(exc)))

    try:
        from app.services.scheduler import create_daily_report_scheduler

        scheduler = create_daily_report_scheduler()
        job_ids = [job.id for job in scheduler.get_jobs()]
        has_file_report = "daily_file_report" in job_ids
        rows.append(
            _row(
                "Scheduler",
                "OK" if has_file_report else "WARNING",
                "Scheduler created with daily file report job." if has_file_report else "Daily file report job not found.",
                {"jobs": job_ids},
            )
        )
    except Exception as exc:
        logger.exception("Scheduler health check failed.")
        rows.append(_row("Scheduler", "ERROR", str(exc)))

    try:
        with SessionLocal() as db:
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
            stale_running = db.execute(
                text(
                    "SELECT COUNT(*) FROM automation_runs "
                    "WHERE status = 'running' AND finished_at IS NULL AND started_at < :cutoff"
                ),
                {"cutoff": cutoff},
            ).scalar() or 0
        rows.append(
            _row(
                "Automation State",
                "WARNING" if int(stale_running) else "OK",
                f"{int(stale_running)} stale running automation row(s) found."
                if int(stale_running)
                else "No stale running automation rows found.",
                {"stale_running_rows": int(stale_running)},
            )
        )
    except Exception as exc:
        logger.exception("Automation state health check failed.")
        rows.append(_row("Automation State", "ERROR", str(exc)))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows.append(_row("Report Folder", "OK", f"Report folder is ready: {REPORT_DIR}"))
    rows.append(_row("Log Folder", "OK", f"Log folder is ready: {LOG_DIR}"))

    if settings is not None:
        if settings.telegram_bot_token:
            rows.append(
                _row(
                    "Telegram Bot",
                    "OK" if settings.allowed_chat_ids else "WARNING",
                    "Bot token is configured; chat/subscriber routing will be used."
                    if settings.allowed_chat_ids
                    else "Bot token is configured but no allowed chat id fallback is configured.",
                    {"token_configured": True, "allowed_chat_ids_configured": bool(settings.allowed_chat_ids)},
                )
            )
        else:
            rows.append(_row("Telegram Bot", "WARNING", "Telegram bot token is not configured.", {"token_configured": False}))

    rows.extend(
        [
            _check_import("app.services.daily_file_report"),
            _check_import("app.services.daily_stock_report"),
            _check_import("app.services.daily_loss_audit"),
            _check_import("app.services.recommendation_performance"),
            _check_import("app.services.end_of_day_review"),
            _check_import("app.services.learning_system"),
            _check_import("app.services.last7_audit"),
            _check_import("app.services.market_daily_evaluation"),
            _check_import("app.services.data_relationships"),
            _check_import("app.services.live_trade_executor"),
            _check_import("app.services.repeated_recommendation_report"),
            _check_import("app.services.system_smoke_test"),
            _check_import("app.services.backtest_cli_v6"),
            _check_import("dashboard.pages.daily_market_evaluation"),
            _check_import("dashboard.pages.trading_control_center"),
            _check_import("dashboard.pages.live_trades"),
            _check_import("dashboard.pages.reports_center"),
            _check_import("dashboard.pages.system_health_admin"),
            _check_import("dashboard.pages.last7_audit"),
            _check_import("dashboard.pages.recommendation_performance"),
            _check_import("dashboard.pages.daily_prediction_review"),
            _check_import("dashboard.pages.missed_opportunities"),
            _check_import("dashboard.pages.why_not_selected"),
            _check_import("dashboard.pages.strategy_learning_center"),
            _check_import("dashboard.pages.source_accuracy"),
            _check_import("dashboard.pages.accuracy_lab"),
            _check_import("dashboard.pages.walk_forward_testing"),
            _check_import("dashboard.pages.pump_risk_monitor"),
            _check_import("dashboard.pages.market_regime"),
            _check_import("dashboard.pages.intraday_scanner"),
            _check_import("dashboard.pages.risk_expectancy"),
            _check_import("dashboard.pages.missed_opportunity_diagnosis"),
            _check_import("dashboard.pages.recommendation_quality"),
            _check_import("dashboard.pages.bot_status"),
        ]
    )

    if save_log:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = LOG_DIR / f"system_health_check_{timestamp}.json"
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.info("System health check saved to %s", output)
    return rows


def format_health_rows(rows: list[dict[str, Any]]) -> str:
    labels = {"OK": "[OK]", "WARNING": "[WARNING]", "ERROR": "[ERROR]"}
    lines = []
    for row in rows:
        label = labels.get(str(row.get("status")), "[INFO]")
        lines.append(f"{label} {row.get('component')}: {row.get('message')}")
    return "\n".join(lines)


def _cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Check EGX system health without exposing secrets.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()
    rows = run_health_check(save_log=True)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_health_rows(rows))


if __name__ == "__main__":
    _cli()
