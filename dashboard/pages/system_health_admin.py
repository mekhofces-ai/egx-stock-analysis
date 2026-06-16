from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.services.automation_runner import get_automation_status, run_automation_cycle
from app.services.daily_file_report import generate_daily_file_report
from app.services.data_relationships import build_data_relationship_report
from app.services.ingestion import run_ingestion_cycle
from app.services.system_health_check import format_health_rows, run_health_check
from app.services.system_smoke_test import format_smoke_result, run_smoke_test
from app.services.telegram_bot import send_private_message_sync
from app.services.trading_safety import disable_trading_for_audit, safety_snapshot
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


CORE_COUNTS = [
    "stocks",
    "telegram_messages",
    "telegram_message_symbols",
    "opportunities",
    "stock_combined_analysis",
    "final_stock_decisions",
    "recommendation_reports",
    "recommendation_items",
    "daily_file_reports",
    "portfolio_trades",
]


def _count_table(db, table: str) -> int | None:  # noqa: ANN001
    try:
        return int(db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
    except Exception:
        return None


def _latest_rows(db) -> dict[str, object]:  # noqa: ANN001
    queries = {
        "last_automation": "SELECT started_at, finished_at, status, symbols_processed, opportunities_count, alerts_sent, error_message FROM automation_runs ORDER BY started_at DESC LIMIT 1",
        "last_recommendation_report": "SELECT report_type, report_time, sent_to_telegram, status, error_message FROM recommendation_reports ORDER BY created_at DESC LIMIT 1",
        "last_file_report": "SELECT report_date, excel_created, pdf_created, sent_to_telegram, status, error_message FROM daily_file_reports ORDER BY created_at DESC LIMIT 1",
        "failed_jobs": "SELECT job_name, status, error_message, created_at FROM jobs_log WHERE status NOT IN ('success','ok','completed') ORDER BY created_at DESC LIMIT 10",
        "stale_automation": "SELECT started_at, status, error_message FROM automation_runs WHERE status='running' AND finished_at IS NULL ORDER BY started_at ASC LIMIT 10",
    }
    result: dict[str, object] = {}
    for key, sql in queries.items():
        try:
            rows = db.execute(text(sql)).mappings().all()
            result[key] = [dict(row) for row in rows]
        except Exception as exc:
            result[key] = [{"error": str(exc)}]
    return result


def _log_tail(limit: int = 200) -> str:
    candidates = sorted(Path("logs").glob("*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in candidates[:5]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if text.strip():
            lines = text.splitlines()[-limit:]
            return f"{path}\n" + "\n".join(lines)
    return "No log output found."


def _counts_df() -> pd.DataFrame:
    with SessionLocal() as db:
        rows = [{"table": table, "rows": _count_table(db, table)} for table in CORE_COUNTS]
    return pd.DataFrame(rows)


def render() -> None:
    st.title("System Health / Admin Control")
    st.caption("Operational controls, health checks, logs, and safe manual jobs. " + RESEARCH_DISCLAIMER)

    with SessionLocal() as db:
        safety = safety_snapshot(db)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Live Trading", "OFF" if not safety.get("live_trading_enabled") else "ON")
    c2.metric("Audit Mode", "ON" if safety.get("audit_mode") else "OFF")
    c3.metric("Emergency Stop", "ON" if safety.get("emergency_stop_trading") else "OFF")
    c4.metric("Execution Blocked", "YES" if safety.get("execution_blocked") else "NO")
    if not safety.get("execution_blocked"):
        warning_box("Execution is not blocked. Use Disable Live Trading before running any automation or bot command.")
    else:
        success_box("Live execution is blocked. The system is in audit/paper mode.")

    actions = st.columns(5)
    if actions[0].button("Fetch Telegram Now"):
        try:
            result = run_ingestion_cycle()
            success_box(f"Telegram fetch finished. New messages: {result.inserted_messages}, analyses: {result.new_analyses}.")
        except Exception as exc:
            warning_box(f"Telegram fetch failed: {exc}")
    if actions[1].button("Run Analysis Now"):
        try:
            result = run_automation_cycle()
            success_box(f"Automation cycle finished with status: {result.get('status')}.")
            key_value_table(result)
        except Exception as exc:
            warning_box(f"Automation cycle failed: {exc}")
    if actions[2].button("Generate Report Now"):
        try:
            result = generate_daily_file_report(force=True, send_telegram=False)
            success_box(f"Daily file report generated. Status: {result.get('status')}.")
            key_value_table(result)
        except Exception as exc:
            warning_box(f"Report generation failed: {exc}")
    if actions[3].button("Send Test Telegram"):
        try:
            send_private_message_sync("EGX system admin test message. Audit/paper mode remains active.")
            success_box("Telegram test message sent to active subscribers.")
        except Exception as exc:
            warning_box(f"Telegram test failed: {exc}")
    if actions[4].button("Run Smoke Test"):
        with st.spinner("Running safe smoke test..."):
            result = run_smoke_test(send_telegram=False)
        st.session_state["system_smoke_result"] = result
        if result.get("status") == "OK":
            success_box("Smoke test passed.")
        else:
            warning_box("Smoke test found issues. See details below.")

    if st.button("Disable Live Trading / Emergency Stop", type="primary"):
        with SessionLocal() as db:
            disable_trading_for_audit(db)
            db.commit()
        warning_box("Live trading disabled and emergency stop enabled.")

    section_title("Health Check")
    if st.button("Refresh Health Check"):
        st.session_state["system_health_rows"] = run_health_check(save_log=True)
    rows = st.session_state.get("system_health_rows") or run_health_check(save_log=False)
    health_df = pd.DataFrame(rows)
    professional_table(health_df, height=360) if not health_df.empty else empty_state("No health rows.")
    with st.expander("Health check text", expanded=False):
        st.text(format_health_rows(rows))

    if st.session_state.get("system_smoke_result"):
        section_title("Last Smoke Test")
        st.text(format_smoke_result(st.session_state["system_smoke_result"]))
        professional_table(pd.DataFrame(st.session_state["system_smoke_result"].get("rows") or []), height=360)

    section_title("Core Data Counts")
    professional_table(_counts_df(), height=360)

    section_title("Data Relationship Check")
    with SessionLocal() as db:
        relation_report = build_data_relationship_report(db)
    key_value_table(relation_report.get("summary") or {})
    rel_df = pd.DataFrame(relation_report.get("table_relationships") or [])
    professional_table(rel_df, height=320) if not rel_df.empty else empty_state("No relationship rows.")
    with st.expander("Duplicate final decisions and missing links", expanded=False):
        dup_df = pd.DataFrame(relation_report.get("duplicate_final_decisions") or [])
        missing_df = pd.DataFrame(relation_report.get("opportunities_without_final_decision") or [])
        st.subheader("Duplicate final decision history")
        professional_table(dup_df, height=260) if not dup_df.empty else empty_state("No duplicate final decisions.")
        st.subheader("Opportunities without final decision")
        professional_table(missing_df, height=260) if not missing_df.empty else empty_state("No orphan opportunities.")

    with SessionLocal() as db:
        latest = _latest_rows(db)
        automation_status = get_automation_status()
    section_title("Automation And Report Status")
    key_value_table(automation_status)
    for label, rows in latest.items():
        st.subheader(label.replace("_", " ").title())
        df = pd.DataFrame(rows)
        professional_table(df, height=240) if not df.empty else empty_state(f"No rows for {label}.")

    section_title("Recent Logs")
    st.text_area("Log tail", value=_log_tail(), height=360)
