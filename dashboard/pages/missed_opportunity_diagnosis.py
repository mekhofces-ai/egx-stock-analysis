from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.learning_common import page_header, show_df
from dashboard.ui_components import section_title, warning_box


def render() -> None:
    _day, payload = page_header("Missed Opportunity Diagnosis", "missed_diagnosis_date")
    df = payload.get("missed_opportunity_diagnosis")
    if isinstance(df, pd.DataFrame) and not df.empty and "Why Not Selected Code" in df.columns:
        section_title("Why Missed")
        counts = df["Why Not Selected Code"].fillna("UNKNOWN").value_counts().reset_index()
        counts.columns = ["Reason", "Count"]
        fig = px.bar(counts, x="Reason", y="Count", color="Reason")
        st.plotly_chart(fig, use_container_width=True)
    else:
        warning_box("No missed-opportunity diagnosis rows yet.")
    show_df(df, "Stock-by-Stock Missed Opportunity Diagnosis")

