from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import REPORT_TIMEZONE, RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import DailyFileReport
from app.services.daily_file_report import (
    REPORT_DIR,
    generate_daily_file_report,
    latest_file_reports,
    send_report_to_telegram,
    target_day,
)
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


def _rows_frame(rows: list[DailyFileReport]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": row.id,
                "report_date": row.report_date.date().isoformat() if row.report_date else "-",
                "report_time": row.report_time,
                "excel_created": row.excel_created,
                "pdf_created": row.pdf_created,
                "sent_to_telegram": row.sent_to_telegram,
                "status": row.status,
                "excel_path": row.excel_path,
                "pdf_path": row.pdf_path,
                "error_message": row.error_message,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


def _latest_report() -> DailyFileReport | None:
    with SessionLocal() as db:
        return db.scalar(select(DailyFileReport).order_by(DailyFileReport.created_at.desc(), DailyFileReport.id.desc()))


def _send_latest_to_telegram(report_id: int) -> dict:
    with SessionLocal() as db:
        row = db.get(DailyFileReport, report_id)
        if row is None:
            return {"status": "missing"}
        result = send_report_to_telegram(row)
        row.sent_to_telegram = bool(result.get("sent_messages") or result.get("sent") or result.get("sent_documents"))
        if not row.sent_to_telegram:
            row.status = "telegram_failed"
            row.error_message = row.error_message or "Telegram failed: no approved/configured chat received the report."
        db.commit()
        return result


def render() -> None:
    st.title("Reports Center")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(f"Daily Excel/PDF report is scheduled for 15:00 {REPORT_TIMEZONE}. Live trading remains disabled.")

    report_date = st.date_input("Report date", value=date.today(), key="reports_center_date")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    send_telegram = c1.checkbox("Send to Telegram", value=False, key="reports_center_send")
    excel_only = c2.checkbox("Excel only", value=False, key="reports_center_excel_only")
    pdf_only = c3.checkbox("PDF only", value=False, key="reports_center_pdf_only")
    force = c4.checkbox("Force regenerate", value=False, key="reports_center_force")

    b1, b2, b3 = st.columns([1, 1, 2])
    if b1.button("Generate Today Report", type="primary"):
        with st.spinner("Generating daily Excel/PDF report..."):
            result = generate_daily_file_report(
                report_date=target_day(report_date.isoformat()),
                send_telegram=send_telegram,
                excel=not pdf_only,
                pdf=not excel_only,
                force=force,
            )
        if result.get("status") == "telegram_failed":
            warning_box(f"Report generated but Telegram send failed. {result.get('error_message') or ''}")
        elif result.get("status") == "duplicate_skipped":
            warning_box(f"Duplicate report skipped. Existing report id: {result.get('report_id')}")
        else:
            success_box(f"Report status: {result.get('status')}. Report id: {result.get('report_id')}")
        key_value_table(result)

    latest = _latest_report()
    if b2.button("Send Report to Telegram"):
        if not latest:
            warning_box("No daily file report exists yet.")
        else:
            with st.spinner("Sending latest report files to Telegram..."):
                result = _send_latest_to_telegram(latest.id)
            if result.get("sent_messages") or result.get("sent"):
                success_box("Latest report notification/documents sent to Telegram subscribers.")
            else:
                warning_box("Telegram send did not reach any approved/configured subscriber.")
            key_value_table(result)

    if b3.button("Open Reports Folder"):
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(REPORT_DIR))  # type: ignore[attr-defined]
            success_box(f"Opened reports folder: {REPORT_DIR}")
        except Exception as exc:
            warning_box(f"Could not open the folder automatically: {exc}. Folder path: {REPORT_DIR}")

    latest = _latest_report()
    section_title("Latest Daily File Report")
    if not latest:
        empty_state("No Excel/PDF daily file report has been generated yet.")
    else:
        key_value_table(
            {
                "report_id": latest.id,
                "report_date": latest.report_date.date().isoformat(),
                "report_time": latest.report_time,
                "excel_created": "yes" if latest.excel_created else "no",
                "pdf_created": "yes" if latest.pdf_created else "no",
                "sent_to_telegram": "yes" if latest.sent_to_telegram else "no",
                "status": latest.status,
                "excel_path": latest.excel_path or "-",
                "pdf_path": latest.pdf_path or "-",
                "error_message": latest.error_message or "-",
            }
        )
        file_cols = st.columns(2)
        if latest.excel_path and Path(latest.excel_path).exists():
            file_cols[0].download_button(
                "Download Excel",
                Path(latest.excel_path).read_bytes(),
                file_name=Path(latest.excel_path).name,
            )
        if latest.pdf_path and Path(latest.pdf_path).exists():
            file_cols[1].download_button(
                "Download PDF",
                Path(latest.pdf_path).read_bytes(),
                file_name=Path(latest.pdf_path).name,
            )

    section_title("Previous Reports")
    with SessionLocal() as db:
        rows = latest_file_reports(db, limit=100)
    frame = _rows_frame(rows)
    professional_table(frame, height=420) if not frame.empty else empty_state("No report history yet.")
