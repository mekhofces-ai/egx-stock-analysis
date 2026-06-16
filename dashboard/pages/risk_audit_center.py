from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import DailyLossAuditItem, DailyLossAuditReport, RecommendationItem, RecommendationReport
from app.services.daily_loss_audit import (
    build_daily_loss_audit,
    export_csv,
    format_telegram_audit,
    parse_audit_date,
    send_telegram_audit,
)
from app.services.trading_safety import disable_trading_for_audit, safety_snapshot
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{key: value for key, value in vars(row).items() if not key.startswith("_")} for row in rows])


def _latest_audit_rows() -> tuple[DailyLossAuditReport | None, list[DailyLossAuditItem]]:
    with SessionLocal() as db:
        report = db.scalar(select(DailyLossAuditReport).order_by(DailyLossAuditReport.created_at.desc(), DailyLossAuditReport.id.desc()))
        items = []
        if report:
            items = db.scalars(select(DailyLossAuditItem).where(DailyLossAuditItem.report_id == report.id).order_by(DailyLossAuditItem.priority.asc(), DailyLossAuditItem.symbol.asc())).all()
    return report, items


def _latest_recommendations() -> pd.DataFrame:
    with SessionLocal() as db:
        report = db.scalar(select(RecommendationReport).order_by(RecommendationReport.created_at.desc(), RecommendationReport.id.desc()))
        if not report:
            return pd.DataFrame()
        items = db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == report.id).order_by(RecommendationItem.final_score.desc())).all()
    rows = _df(items)
    if rows.empty:
        return rows
    columns = [
        "symbol",
        "signal",
        "final_score",
        "telegram_score",
        "technical_score",
        "strategy_score",
        "news_score",
        "backtest_score",
        "risk_liquidity_score",
        "entry_zone_low",
        "entry_zone_high",
        "stop_loss",
        "risk_reward",
    ]
    return rows[[col for col in columns if col in rows.columns]]


def render() -> None:
    st.title("Risk & Audit Center")
    st.caption("Audit, simulation, emergency stop, and recommendation-loss review. " + RESEARCH_DISCLAIMER)

    with SessionLocal() as db:
        snapshot = safety_snapshot(db)
    box = warning_box if snapshot.get("execution_blocked") else success_box
    box(
        "Emergency/audit safety is active. Live trading and automatic execution are blocked."
        if snapshot.get("execution_blocked")
        else "No emergency block is currently active."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Audit mode", "ON" if snapshot.get("audit_mode") else "OFF")
    c2.metric("Emergency stop", "ON" if snapshot.get("emergency_stop_trading") else "OFF")
    c3.metric("Live trading", "ON" if snapshot.get("live_trading_enabled") else "OFF")
    c4.metric("Daily loss", f"{snapshot.get('daily_loss_pct', 0):.2f}%", f"Limit {snapshot.get('daily_loss_limit_pct', 0):.2f}%")
    key_value_table(
        {
            "Execution blocked": "Yes" if snapshot.get("execution_blocked") else "No",
            "Blocked reasons": ", ".join(snapshot.get("blocked_reasons") or ["None"]),
            "Portfolio auto scan": "On" if snapshot.get("portfolio_auto_scan") else "Off",
            "Auto paper execution": "On" if snapshot.get("portfolio_auto_execute_paper_trades") else "Off",
        }
    )

    a1, a2, a3, a4 = st.columns(4)
    audit_date = a1.date_input("Audit date", value=date.today(), key="risk_audit_date")
    if a2.button("Run Audit Now"):
        result = build_daily_loss_audit(target_date=parse_audit_date(audit_date.isoformat()), persist=True)
        st.session_state["risk_audit_result"] = result
        success_box(f"Audit created. Total recommendations: {result['summary']['total_recommendations']}.")
    if a3.button("Send Audit Report to Telegram"):
        result = build_daily_loss_audit(target_date=parse_audit_date(audit_date.isoformat()), persist=True)
        send_telegram_audit(result)
        st.session_state["risk_audit_result"] = result
        success_box("Audit report sent to Telegram.")
    if a4.button("Disable Live Trading"):
        with SessionLocal() as db:
            disable_trading_for_audit(db)
            db.commit()
        warning_box("Live trading disabled, emergency stop enabled, and portfolio automation stopped.")

    if st.session_state.get("risk_audit_result"):
        result = st.session_state["risk_audit_result"]
        if st.button("Export Last Audit CSV"):
            path = export_csv(result)
            success_box(f"CSV exported: {path}")
        with st.expander("Telegram audit preview", expanded=False):
            st.text_area("Preview", value=format_telegram_audit(result), height=320)

    report, audit_items = _latest_audit_rows()
    section_title("Latest Audit Summary")
    if not report:
        empty_state("No audit report has been created yet. Run Audit Now to generate one.")
    else:
        cols = st.columns(6)
        cols[0].metric("Date", report.audit_date.date().isoformat())
        cols[1].metric("Total", report.total_recommendations)
        cols[2].metric("Good", report.good_calls)
        cols[3].metric("Bad", report.bad_calls)
        cols[4].metric("No Entry", report.no_entry)
        cols[5].metric("Stop Hit", report.stop_loss_hit)
        warning_box(f"Biggest problem: {report.biggest_problem or '-'}")
        st.write(report.final_diagnosis or "-")

    section_title("Bad Calls And Root Causes")
    item_df = _df(audit_items)
    if item_df.empty:
        empty_state("No audit item rows yet.")
    else:
        visible = [
            "symbol",
            "recommendation",
            "final_score",
            "result",
            "mistake_type",
            "actual_return",
            "max_drawdown_after_entry",
            "root_cause",
            "fix_required",
            "priority",
            "created_at",
        ]
        bad_df = item_df[item_df["result"].isin(["BAD_CALL", "OPEN_LOSS", "BAD_ENTRY", "RISK_PROBLEM", "LOW_LIQUIDITY", "DATA_PROBLEM", "LATE_SIGNAL"])] if "result" in item_df else item_df
        professional_table(bad_df[[col for col in visible if col in bad_df.columns]], height=420)

    section_title("Latest Recommendations Under Review")
    rec_df = _latest_recommendations()
    professional_table(rec_df, height=420) if not rec_df.empty else empty_state("No recommendation report rows found yet.")
