from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.learning_common import page_header, show_df
from dashboard.ui_components import key_value_table, section_title, warning_box


def render() -> None:
    _day, payload = page_header("Walk-Forward Testing", "walk_forward_date")
    summary = payload.get("walk_forward_summary") or {}
    if summary:
        key_value_table(summary)
    periods = payload.get("walk_forward_periods")
    if isinstance(periods, pd.DataFrame) and not periods.empty:
        section_title("In-Sample vs Out-of-Sample")
        chart_df = periods.melt(
            id_vars=["Period"],
            value_vars=["In-Sample Win Rate %", "Out-of-Sample Win Rate %"],
            var_name="Metric",
            value_name="Win Rate %",
        )
        fig = px.line(chart_df, x="Period", y="Win Rate %", color="Metric", markers=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        warning_box("Not enough evaluated recommendation history for rolling walk-forward periods.")
    show_df(periods, "Walk-Forward Periods")

