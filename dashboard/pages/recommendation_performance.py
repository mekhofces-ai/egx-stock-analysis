from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.services.recommendation_performance import (
    build_performance_frames,
    generate_performance_excel,
    run_daily_re_evaluation,
    send_performance_report_to_telegram,
)
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


def _options(df: pd.DataFrame, column: str) -> list[str]:
    if df.empty or column not in df.columns:
        return []
    values = sorted(str(value) for value in df[column].dropna().unique() if str(value).strip())
    return values


def _filter(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    with st.expander("Filters", expanded=True):
        c1, c2, c3 = st.columns(3)
        date_from = c1.date_input("From recommendation date", value=None, key="perf_date_from")
        date_to = c2.date_input("To recommendation date", value=None, key="perf_date_to")
        symbol_text = c3.text_input("Stock search", key="perf_symbol_search", placeholder="COMI, AALR...")
        c4, c5, c6, c7 = st.columns(4)
        stage = c4.multiselect("Recommendation stage", _options(df, "Recommendation Stage"), key="perf_stage")
        strategy = c5.multiselect("Strategy", _options(df, "Strategy"), key="perf_strategy")
        telegram = c6.multiselect("Telegram source", _options(df, "Telegram Source"), key="perf_telegram")
        status = c7.multiselect("Status", _options(df, "Status"), key="perf_status")
        quality = st.multiselect("Quality", _options(df, "Quality"), key="perf_quality")
    out = df.copy()
    if "Recommendation Date" in out.columns:
        dates = pd.to_datetime(out["Recommendation Date"], errors="coerce").dt.date
        if date_from:
            out = out[dates >= date_from]
            dates = pd.to_datetime(out["Recommendation Date"], errors="coerce").dt.date
        if date_to:
            out = out[dates <= date_to]
    if symbol_text:
        needle = symbol_text.strip().upper()
        out = out[out["Stock Symbol"].astype(str).str.upper().str.contains(needle, na=False)]
    if stage:
        out = out[out["Recommendation Stage"].astype(str).isin(stage)]
    if strategy:
        out = out[out["Strategy"].astype(str).isin(strategy)]
    if telegram:
        out = out[out["Telegram Source"].astype(str).isin(telegram)]
    if status:
        out = out[out["Status"].astype(str).isin(status)]
    if quality:
        out = out[out["Quality"].astype(str).isin(quality)]
    return out


def render() -> None:
    st.title("Recommendation Performance")
    st.caption(RESEARCH_DISCLAIMER)
    st.info("This page re-checks old recommendations using only candles after the recommendation timestamp. Live trading remains disabled.")

    c1, c2, c3 = st.columns([1, 1, 2])
    as_of = c1.date_input("Evaluation date", value=date.today(), key="recommendation_perf_date")
    if c1.button("Run Re-evaluation Now", type="primary"):
        with st.spinner("Re-checking previous recommendations..."):
            with SessionLocal() as db:
                result = run_daily_re_evaluation(as_of_date=as_of, db=db)
        success_box(f"Updated {len(result.get('updated') or [])} recommendation evaluation row(s).")
        key_value_table(result.get("summary") or {})
    if c2.button("Generate Excel"):
        with st.spinner("Generating recommendation performance workbook..."):
            path = generate_performance_excel(as_of_date=as_of)
        success_box(f"Excel generated: {path}")
    if c3.button("Send Telegram Performance Report"):
        with st.spinner("Sending recommendation performance report to Telegram subscribers..."):
            result = send_performance_report_to_telegram(as_of_date=as_of, force=False)
        if result.get("status") == "duplicate_skipped":
            warning_box("Telegram report already sent for this date. Duplicate blocked.")
        elif result.get("sent"):
            success_box("Telegram performance report sent.")
        else:
            warning_box(f"Telegram send failed or no subscriber received it: {result}")
        key_value_table(result)

    with SessionLocal() as db:
        frames = build_performance_frames(db)
    summary_df = frames.get("summary", pd.DataFrame())
    summary = {row["Metric"]: row["Value"] for row in summary_df.to_dict("records")} if not summary_df.empty else {}

    cols = st.columns(6)
    cols[0].metric("Total", summary.get("total_recommendations", 0))
    cols[1].metric("Open", summary.get("open_recommendations", 0))
    cols[2].metric("Evaluated", summary.get("evaluated_recommendations", 0))
    cols[3].metric("Win Rate", "-" if summary.get("win_rate_pct") is None else f"{summary.get('win_rate_pct')}%")
    cols[4].metric("Avg Return", "-" if summary.get("average_return_pct") is None else f"{summary.get('average_return_pct')}%")
    cols[5].metric("Targets / Stops", f"{summary.get('target_hits_today', 0)} / {summary.get('stop_hits_today', 0)}")
    if summary.get("accuracy_note"):
        warning_box(str(summary["accuracy_note"]))

    tabs = st.tabs([
        "Stock-by-Stock Recommendation Review",
        "Accuracy by Stage",
        "Accuracy by Strategy",
        "Accuracy by Telegram",
        "Accuracy by Market",
    ])
    with tabs[0]:
        section_title("Stock-by-Stock Recommendation Review")
        df = frames.get("stock_by_stock", pd.DataFrame())
        filtered = _filter(df)
        professional_table(filtered, height=560) if not filtered.empty else empty_state("No recommendation evaluations stored yet.")
    with tabs[1]:
        professional_table(frames.get("accuracy_by_stage", pd.DataFrame()), height=360)
    with tabs[2]:
        professional_table(frames.get("accuracy_by_strategy", pd.DataFrame()), height=360)
    with tabs[3]:
        professional_table(frames.get("accuracy_by_telegram_source", pd.DataFrame()), height=360)
    with tabs[4]:
        professional_table(frames.get("accuracy_by_market_condition", pd.DataFrame()), height=360)
