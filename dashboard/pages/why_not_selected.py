from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.pages.end_of_day_common import header
from dashboard.ui_components import empty_state, professional_table, section_title


def render() -> None:
    _day, payload = header("Why Not Selected", "why_not_selected_date")
    df = payload.get("why_not_selected")
    section_title("Every Stock Selection Decision")
    if df is None or df.empty:
        empty_state("No why-not-selected rows available.")
        return
    c1, c2, c3 = st.columns(3)
    symbol = c1.text_input("Stock search", key="why_not_selected_symbol")
    reason_options = sorted(str(value) for value in df.get("Why Not Selected Code", pd.Series(dtype=str)).dropna().unique())
    reason = c2.multiselect("Reason", reason_options, key="why_not_selected_reason")
    selected_filter = c3.selectbox("Selected today", ["All", "Selected", "Not selected"], key="why_not_selected_selected")
    out = df.copy()
    if symbol:
        out = out[out["Stock Symbol"].astype(str).str.upper().str.contains(symbol.strip().upper(), na=False)]
    if reason:
        out = out[out["Why Not Selected Code"].astype(str).isin(reason)]
    if selected_filter == "Selected":
        out = out[out["Selected Today"].astype(bool)]
    elif selected_filter == "Not selected":
        out = out[~out["Selected Today"].astype(bool)]
    professional_table(out, height=620)
