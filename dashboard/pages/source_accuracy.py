from __future__ import annotations

import plotly.express as px
import pandas as pd
import streamlit as st

from dashboard.pages.end_of_day_common import header, show_table
from dashboard.ui_components import empty_state, section_title


def _scatter(df: pd.DataFrame, score_col: str, title: str) -> None:
    if df is None or df.empty or score_col not in df.columns or "Actual Return %" not in df.columns:
        empty_state(f"No data for {title}.")
        return
    chart_df = df.copy()
    chart_df[score_col] = pd.to_numeric(chart_df[score_col], errors="coerce")
    chart_df["Actual Return %"] = pd.to_numeric(chart_df["Actual Return %"], errors="coerce")
    chart_df = chart_df.dropna(subset=[score_col, "Actual Return %"])
    if chart_df.empty:
        empty_state(f"No numeric rows for {title}.")
        return
    fig = px.scatter(chart_df, x=score_col, y="Actual Return %", color="Selected", hover_name="Stock Symbol", title=title)
    st.plotly_chart(fig, use_container_width=True)


def render() -> None:
    _day, payload = header("Source Accuracy", "source_accuracy_date")
    tabs = st.tabs(["Telegram", "Technical", "Financial", "News"])
    with tabs[0]:
        section_title("Telegram vs Actual")
        _scatter(payload.get("telegram_vs_actual"), "Telegram Score", "Telegram Score vs Actual Return")
        show_table(payload, "telegram_vs_actual", "Telegram Rows", height=320)
    with tabs[1]:
        section_title("Technical vs Actual")
        _scatter(payload.get("technical_vs_actual"), "Technical Score", "Technical Score vs Actual Return")
        show_table(payload, "technical_vs_actual", "Technical Rows", height=320)
    with tabs[2]:
        section_title("Financial vs Actual")
        _scatter(payload.get("financial_vs_actual"), "Financial Score", "Financial Score vs Actual Return")
        show_table(payload, "financial_vs_actual", "Financial Rows", height=320)
    with tabs[3]:
        section_title("News vs Actual")
        _scatter(payload.get("news_vs_actual"), "News Score", "News Score vs Actual Return")
        show_table(payload, "news_vs_actual", "News Rows", height=320)
