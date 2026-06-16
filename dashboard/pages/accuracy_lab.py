from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.learning_common import page_header, show_df, show_expectancy_cards
from dashboard.ui_components import empty_state, section_title, warning_box


def render() -> None:
    _day, payload = page_header("Accuracy Lab", "accuracy_lab_date")
    show_expectancy_cards(payload)
    source_df = payload.get("source_accuracy")
    if isinstance(source_df, pd.DataFrame) and not source_df.empty and "Reliability Score" in source_df.columns:
        section_title("Source Reliability")
        chart_df = source_df.copy()
        chart_df["Reliability Score"] = pd.to_numeric(chart_df["Reliability Score"], errors="coerce")
        fig = px.bar(chart_df, x="Source", y="Reliability Score", color="Source", title="Reliability by Source")
        st.plotly_chart(fig, use_container_width=True)
    else:
        warning_box("Source accuracy needs evaluated recommendations before reliability becomes meaningful.")
    show_df(source_df, "Source Accuracy Detail")
    show_df(payload.get("recommendation_quality"), "Recommendation Quality")
    show_df(payload.get("decision_snapshots"), "Decision Snapshots")

