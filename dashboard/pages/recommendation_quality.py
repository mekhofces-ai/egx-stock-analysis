from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.learning_common import page_header, show_df
from dashboard.ui_components import section_title, warning_box


def render() -> None:
    _day, payload = page_header("Recommendation Quality", "recommendation_quality_date")
    df = payload.get("recommendation_quality")
    if isinstance(df, pd.DataFrame) and not df.empty and "final_quality_score" in df.columns:
        section_title("Quality Scores")
        chart_df = df.copy()
        chart_df["final_quality_score"] = pd.to_numeric(chart_df["final_quality_score"], errors="coerce")
        fig = px.bar(chart_df.sort_values("final_quality_score", ascending=False), x="Stock Symbol", y="final_quality_score", color="quality_grade")
        st.plotly_chart(fig, use_container_width=True)
    else:
        warning_box("No recommendation quality snapshots yet.")
    show_df(df, "Recommendation Quality Breakdown")
