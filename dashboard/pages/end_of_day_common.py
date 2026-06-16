from __future__ import annotations

from datetime import date

import streamlit as st

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.services.end_of_day_review import build_end_of_day_review, generate_end_of_day_review
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


def date_control(key: str) -> date:
    return st.date_input("Review date", value=date.today(), key=key)


@st.cache_data(ttl=300, show_spinner=False)
def load_payload(day: date) -> dict:
    with SessionLocal() as db:
        return build_end_of_day_review(target_date=day, persist=False, db=db)


def header(title: str, key: str) -> tuple[date, dict]:
    st.title(title)
    st.caption(RESEARCH_DISCLAIMER)
    st.info("Audit/paper mode only. Live trading and real order execution remain disabled.")
    c1, c2, c3 = st.columns([1, 1, 2])
    day = c1.date_input("Review date", value=date.today(), key=key)
    if c2.button("Refresh Review", key=f"{key}_refresh"):
        load_payload.clear()
        st.rerun()
    if c3.button("Generate Excel Sample", key=f"{key}_excel"):
        with st.spinner("Generating end-of-day review workbook..."):
            result = generate_end_of_day_review(target_date=day, dry_run=True, persist=False)
        success_box(f"Excel generated: {result.get('excel_path')}")
    with st.spinner("Loading end-of-day review..."):
        payload = load_payload(day)
    summary = payload.get("summary") or {}
    cols = st.columns(6)
    cols[0].metric("Recommendations", summary.get("total_recommendations", 0))
    cols[1].metric("Evaluated", summary.get("evaluated_recommendations", 0))
    cols[2].metric("Target / Stop", f"{summary.get('target_hits', 0)} / {summary.get('stop_hits', 0)}")
    cols[3].metric("Entry Not Reached", summary.get("entry_not_reached", 0))
    cols[4].metric("Avg Return", "-" if summary.get("average_return_pct") is None else f"{summary.get('average_return_pct')}%")
    cols[5].metric("Win Rate", "-" if summary.get("win_rate_pct") is None else f"{summary.get('win_rate_pct')}%")
    if summary.get("accuracy_note"):
        warning_box(str(summary["accuracy_note"]))
    return day, payload


def show_table(payload: dict, key: str, title: str, height: int = 520) -> None:
    section_title(title)
    df = payload.get(key)
    if df is None or df.empty:
        empty_state("No rows available for this section.")
        return
    professional_table(df, height=height)


def show_summary(payload: dict) -> None:
    section_title("Review Summary")
    key_value_table(payload.get("summary") or {})
