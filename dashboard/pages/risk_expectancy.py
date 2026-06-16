from __future__ import annotations

import streamlit as st

from dashboard.pages.learning_common import page_header, show_df, show_expectancy_cards


def render() -> None:
    _day, payload = page_header("Risk & Expectancy", "risk_expectancy_date")
    show_expectancy_cards(payload)
    show_df(payload.get("risk_expectancy"), "Risk & Expectancy Detail", height=220)
    show_df(payload.get("source_accuracy"), "Source Reliability Context")

