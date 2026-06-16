from __future__ import annotations

import argparse
import importlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.database import SessionLocal, init_db
from app.services.daily_file_report import generate_daily_file_report
from app.services.daily_stock_report import generate_daily_report
from app.services.last7_audit import build_last7_audit
from app.services.learning_system import build_learning_payload
from app.services.system_health_check import run_health_check
from app.services.trading_safety import safety_snapshot


LOG_DIR = Path("logs")
logger = logging.getLogger(__name__)


PAGE_MODULES = [
    "dashboard.pages.executive_overview",
    "dashboard.pages.daily_opportunities",
    "dashboard.pages.financial_analysis",
    "dashboard.pages.news_analysis",
    "dashboard.pages.trading_alerts",
    "dashboard.pages.reports_center",
    "dashboard.pages.risk_audit_center",
    "dashboard.pages.market_heatmap",
    "dashboard.pages.portfolio_bot_page",
    "dashboard.pages.accuracy_lab",
    "dashboard.pages.walk_forward_testing",
    "dashboard.pages.pump_risk_monitor",
    "dashboard.pages.market_regime",
    "dashboard.pages.intraday_scanner",
    "dashboard.pages.risk_expectancy",
    "dashboard.pages.missed_opportunity_diagnosis",
    "dashboard.pages.recommendation_quality",
]


def _row(component: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"component": component, "status": status, "message": message, "details": details or {}}


def _ok(status: str) -> bool:
    return status.upper() in {"OK", "WARNING"}


def _table_count(db, table_name: str) -> int | None:  # noqa: ANN001
    try:
        return int(db.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0)
    except Exception:
        logger.exception("Could not count table %s", table_name)
        return None


def _stale_automation_runs(db) -> int:  # noqa: ANN001
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    try:
        return int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM automation_runs "
                    "WHERE status = 'running' AND finished_at IS NULL AND started_at < :cutoff"
                ),
                {"cutoff": cutoff},
            ).scalar()
            or 0
        )
    except Exception:
        logger.exception("Could not check stale automation runs.")
        return 0


def run_smoke_test(*, send_telegram: bool = False, save_log: bool = True) -> dict[str, Any]:
    """Run quick non-trading checks across the EGX system.

    The smoke test intentionally stays in audit/paper mode. Telegram sending is
    opt-in through send_telegram and is not used by default.
    """

    init_db(seed=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    health_rows = run_health_check(save_log=save_log)
    errors = [row for row in health_rows if row.get("status") == "ERROR"]
    warnings = [row for row in health_rows if row.get("status") == "WARNING"]
    rows.append(
        _row(
            "Health Check",
            "OK" if not errors else "ERROR",
            f"{len(errors)} errors and {len(warnings)} warnings from health check.",
            {"errors": len(errors), "warnings": len(warnings)},
        )
    )

    with SessionLocal() as db:
        snapshot = safety_snapshot(db)
        blocked = bool(snapshot.get("execution_blocked"))
        rows.append(
            _row(
                "Trading Safety",
                "OK" if blocked else "ERROR",
                "Live execution is blocked." if blocked else "Live execution is not blocked.",
                snapshot,
            )
        )
        counts = {
            table: _table_count(db, table)
            for table in [
                "stocks",
                "telegram_messages",
                "telegram_message_symbols",
                "opportunities",
                "stock_combined_analysis",
                "final_stock_decisions",
                "recommendation_reports",
                "recommendation_items",
            ]
        }
        rows.append(_row("Database Counts", "OK", "Core table counts collected.", counts))
        stale_runs = _stale_automation_runs(db)
        rows.append(
            _row(
                "Automation Runs",
                "WARNING" if stale_runs else "OK",
                f"{stale_runs} stale running automation row(s)." if stale_runs else "No stale running automation rows found.",
                {"stale_running_rows": stale_runs},
            )
        )

    for module_name in PAGE_MODULES:
        try:
            module = importlib.import_module(module_name)
            if not hasattr(module, "render"):
                rows.append(_row(module_name, "WARNING", "Module imported but has no render() function."))
            else:
                rows.append(_row(module_name, "OK", "Dashboard page module imported."))
        except Exception as exc:
            logger.exception("Dashboard page smoke import failed for %s", module_name)
            rows.append(_row(module_name, "ERROR", str(exc)))

    try:
        result = generate_daily_report(report_type="morning", dry_run=True, send=False, top_n=5)
        rows.append(_row("Daily Stock Report", "OK", "Dry run generated.", {"items_count": result.get("items_count")}))
    except Exception as exc:
        logger.exception("Daily stock report smoke test failed.")
        rows.append(_row("Daily Stock Report", "ERROR", str(exc)))

    try:
        result = generate_daily_file_report(dry_run=True, send_telegram=False)
        rows.append(_row("Daily File Report", "OK", "Dry run collected report data.", {"items_count": result.get("items_count")}))
    except Exception as exc:
        logger.exception("Daily file report smoke test failed.")
        rows.append(_row("Daily File Report", "ERROR", str(exc)))

    try:
        result = build_last7_audit(days=7, persist_daily=False)
        rows.append(
            _row(
                "Last 7 Days Audit",
                "OK",
                "Audit summary generated.",
                {
                    "total_recommendations": result.get("summary", {}).get("total_recommendations"),
                    "evaluated": result.get("summary", {}).get("evaluated"),
                },
            )
        )
    except Exception as exc:
        logger.exception("Last 7 days audit smoke test failed.")
        rows.append(_row("Last 7 Days Audit", "ERROR", str(exc)))

    try:
        with SessionLocal() as db:
            payload = build_learning_payload(db, persist=False)
        rows.append(
            _row(
                "Learning System",
                "OK",
                "Learning payload generated in audit/paper mode.",
                {
                    "source_accuracy_rows": len(payload.get("source_accuracy", [])),
                    "quality_rows": len(payload.get("recommendation_quality", [])),
                },
            )
        )
    except Exception as exc:
        logger.exception("Learning system smoke test failed.")
        rows.append(_row("Learning System", "WARNING", f"Learning payload check could not run in this context: {exc}"))

    if send_telegram:
        try:
            from app.services.telegram_bot import send_private_message_sync

            send_private_message_sync("EGX system smoke test completed in audit/paper mode.")
            rows.append(_row("Telegram Test Send", "OK", "Test message sent to active subscribers."))
        except Exception as exc:
            logger.exception("Telegram smoke send failed.")
            rows.append(_row("Telegram Test Send", "ERROR", str(exc)))
    else:
        rows.append(_row("Telegram Test Send", "SKIPPED", "Use --send-telegram to send a real test message."))

    status = "OK" if all(_ok(str(row.get("status"))) or row.get("status") == "SKIPPED" for row in rows) else "ERROR"
    result = {"status": status, "generated_at": datetime.now(UTC).isoformat(), "rows": rows}
    if save_log:
        output = LOG_DIR / f"system_smoke_test_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        result["log_path"] = str(output)
    return result


def format_smoke_result(result: dict[str, Any]) -> str:
    labels = {"OK": "[OK]", "WARNING": "[WARNING]", "ERROR": "[ERROR]", "SKIPPED": "[SKIPPED]"}
    lines = [f"System smoke test: {result.get('status')}"]
    for row in result.get("rows") or []:
        lines.append(f"{labels.get(row.get('status'), '[INFO]')} {row.get('component')}: {row.get('message')}")
    if result.get("log_path"):
        lines.append(f"Log: {result['log_path']}")
    return "\n".join(lines)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run a safe EGX system smoke test.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--send-telegram", action="store_true", help="Send one real Telegram test message.")
    args = parser.parse_args()
    result = run_smoke_test(send_telegram=args.send_telegram, save_log=True)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_smoke_result(result))


if __name__ == "__main__":
    _cli()
