from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.learning_common import page_header, show_df
from dashboard.ui_components import section_title, warning_box


def render() -> None:
    _day, payload = page_header("Pump Risk Monitor", "pump_risk_date")
    df = payload.get("pump_risk_monitor")
    if isinstance(df, pd.DataFrame) and not df.empty and "pump_risk_score" in df.columns:
        section_title("Pump Risk Scores")
        chart_df = df.copy()
        chart_df["pump_risk_score"] = pd.to_numeric(chart_df["pump_risk_score"], errors="coerce")
        fig = px.bar(chart_df.sort_values("pump_risk_score", ascending=False), x="Stock Symbol", y="pump_risk_score", color="risk_level")
        st.plotly_chart(fig, use_container_width=True)
    else:
        warning_box("No pump-risk rows yet. They are created when recommendations or learning scans run.")
    show_df(df, "Pump Risk Details")

