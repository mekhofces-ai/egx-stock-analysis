from __future__ import annotations

import pandas as pd
import streamlit as st

from app.config import RESEARCH_DISCLAIMER
from app.services.last7_audit import build_last7_audit, export_last7_csv, format_last7_summary
from dashboard.ui_components import empty_state, key_value_table, professional_table, section_title, success_box, warning_box


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def render() -> None:
    st.title("Last 7 Days Audit")
    st.caption("Recommendation accuracy, repeated stocks, missed opportunities, and failure reasons. " + RESEARCH_DISCLAIMER)

    c1, c2, c3 = st.columns([1, 1, 2])
    days = c1.slider("Review window", 3, 30, 7, step=1)
    persist_daily = c2.checkbox("Persist daily audit rows", value=False)
    run_now = c3.button("Run Last 7 Days Review", type="primary")

    if run_now or "last7_audit_result" not in st.session_state:
        with st.spinner("Reviewing recommendations against stored market data..."):
            st.session_state["last7_audit_result"] = build_last7_audit(days=days, persist_daily=persist_daily)
    result = st.session_state["last7_audit_result"]
    summary = result.get("summary") or {}

    cols = st.columns(6)
    cols[0].metric("Recommendations", summary.get("total_recommendations", 0))
    cols[1].metric("Evaluated", summary.get("evaluated", 0))
    cols[2].metric("Win Rate", f"{summary.get('win_rate', 0)}%")
    cols[3].metric("Avg Return", summary.get("average_return", "-"))
    cols[4].metric("Max DD", summary.get("max_drawdown", "-"))
    cols[5].metric("Repeated", summary.get("repeated_symbols", 0))

    if int(summary.get("evaluated") or 0) == 0:
        warning_box("No recommendation rows had enough post-signal price data in this window. Import/update OHLCV data for stronger validation.")
    else:
        warning_box(f"Top failure reason: {summary.get('top_failure_reason') or 'None'}")

    with st.expander("Text summary", expanded=False):
        st.text_area("Summary", value=format_last7_summary(result), height=260)
    if st.button("Export Last 7 Days CSV"):
        path = export_last7_csv(result)
        success_box(f"CSV exported: {path}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "Worst Recommendations",
            "Best Recommendations",
            "Repeated Stocks",
            "Telegram Mentions",
            "Missed Opportunities",
            "All Audit Rows",
        ]
    )
    with tab1:
        df = _frame(result.get("worst_recommendations"))
        if df.empty:
            empty_state("No evaluated losing recommendation rows yet.")
        else:
            professional_table(df, height=420)
    with tab2:
        df = _frame(result.get("best_recommendations"))
        professional_table(df, height=420) if not df.empty else empty_state("No evaluated winning recommendation rows yet.")
    with tab3:
        df = _frame(result.get("repeated_stocks"))
        if df.empty:
            empty_state("No repeated stock/recommendation combinations in this period.")
        else:
            professional_table(df, height=420)
            warning_box("Deduplication policy should prevent same stock + same recommendation from being sent repeatedly on the same day.")
    with tab4:
        df = _frame(result.get("telegram_mentions"))
        professional_table(df, height=420) if not df.empty else empty_state("No Telegram symbol mentions found in this period.")
    with tab5:
        df = _frame(result.get("missed_opportunities"))
        professional_table(df, height=420) if not df.empty else empty_state("No missed opportunity candidates detected from available price data.")
    with tab6:
        df = _frame(result.get("audit_rows"))
        professional_table(df, height=520) if not df.empty else empty_state("No audit rows were generated.")

    section_title("What To Improve")
    key_value_table(
        {
            "main_failure_reason": summary.get("top_failure_reason") or "-",
            "data_gap": "Low evaluated count means OHLCV coverage needs refresh." if int(summary.get("evaluated") or 0) < int(summary.get("total_recommendations") or 0) else "Coverage looks usable.",
            "duplicate_risk": f"{summary.get('repeated_symbols', 0)} repeated stock/signal rows found.",
            "risk_note": result.get("risk_note"),
        }
    )
