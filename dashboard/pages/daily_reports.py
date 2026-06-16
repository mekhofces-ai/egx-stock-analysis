from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import REPORT_TIMEZONE, RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import RecommendationItem, RecommendationReport
from app.services.daily_stock_report import generate_daily_report, latest_report, latest_report_items
from dashboard.ui_components import key_value_table, professional_table, section_title, success_box, warning_box


def _items_frame(rows: list[RecommendationItem]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": row.symbol,
                "company_name": row.company_name,
                "signal": row.signal,
                "final_score": row.final_score,
                "telegram": row.telegram_score,
                "technical": row.technical_score,
                "strategy": row.strategy_score,
                "news": row.news_score,
                "backtest": row.backtest_score,
                "liquidity_risk": row.risk_liquidity_score,
                "entry_low": row.entry_zone_low,
                "entry_high": row.entry_zone_high,
                "stop_loss": row.stop_loss,
                "target_1": row.target_1,
                "target_2": row.target_2,
                "target_3": row.target_3,
                "risk_reward": row.risk_reward,
            }
            for row in rows
        ]
    )


def _report_panel(report_type: str) -> None:
    with SessionLocal() as db:
        report = latest_report(db, report_type=report_type)
        items = latest_report_items(db, report.id) if report else []
    section_title(f"Latest {report_type.title()} Report")
    if not report:
        warning_box(f"No {report_type} daily stock report has been generated yet.")
        return
    key_value_table(
        {
            "report_id": report.id,
            "report_type": report.report_type,
            "report_time": report.report_time,
            "status": report.status,
            "sent_to_telegram": "yes" if report.sent_to_telegram else "no",
            "error_message": report.error_message or "-",
            "items": len(items),
        }
    )
    professional_table(_items_frame(items), height=310)
    if items:
        selected = st.selectbox(
            f"{report_type.title()} explanation",
            [f"{row.symbol} | {row.signal or '-'} | {float(row.final_score or 0):.0f}" for row in items],
            key=f"daily_report_explain_{report_type}",
        )
        row = items[[f"{item.symbol} | {item.signal or '-'} | {float(item.final_score or 0):.0f}" for item in items].index(selected)]
        st.text_area("Explanation", value=row.explanation or "-", height=180, key=f"daily_report_reason_{report_type}")


def render() -> None:
    st.title("Daily Reports")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(f"Automatic reports are scheduled for 09:00 and 21:00 {REPORT_TIMEZONE}. Reports use stored real data and neutral fallbacks when a source is missing.")

    c1, c2, c3 = st.columns([1, 1, 2])
    report_type = c1.selectbox("Report type", ["morning", "evening"], key="daily_reports_type")
    send_now = c2.checkbox("Send to Telegram", value=False, key="daily_reports_send_now")
    force = c3.checkbox("Force send if already sent today", value=False, key="daily_reports_force")
    b1, b2 = st.columns([1, 3])
    if b1.button("Generate Report Now"):
        with st.spinner("Generating daily stock report..."):
            result = generate_daily_report(report_type=report_type, send=send_now, force=force)
        if result.get("sent"):
            success_box(f"Report sent to Telegram. Report id: {result.get('report_id')}")
        elif result.get("skipped_duplicate"):
            warning_box(f"Duplicate send skipped. Existing report id: {result.get('report_id')}")
        else:
            success_box(f"Report generated. Report id: {result.get('report_id')}")
        st.text_area("Telegram message preview", value=result.get("message", ""), height=430)

    with SessionLocal() as db:
        recent = db.scalars(select(RecommendationReport).order_by(RecommendationReport.created_at.desc()).limit(20)).all()
    section_title("Recent Report Runs")
    professional_table(
        pd.DataFrame(
            [
                {
                    "id": row.id,
                    "type": row.report_type,
                    "report_time": row.report_time,
                    "created_at": row.created_at,
                    "status": row.status,
                    "sent": row.sent_to_telegram,
                    "error": row.error_message,
                }
                for row in recent
            ]
        ),
        height=260,
    )

    tabs = st.tabs(["Morning", "Evening"])
    with tabs[0]:
        _report_panel("morning")
    with tabs[1]:
        _report_panel("evening")
