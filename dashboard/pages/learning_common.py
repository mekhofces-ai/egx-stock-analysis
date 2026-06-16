from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.database import SessionLocal
from app.services.learning_system import build_learning_payload
from dashboard.ui_components import empty_state, metric_card, professional_table, section_title, warning_box


@st.cache_data(ttl=300, show_spinner=False)
def load_learning_payload(day: date) -> dict:
    with SessionLocal() as db:
        return build_learning_payload(db, target_date=day, persist=False)


def page_header(title: str, key: str) -> tuple[date, dict]:
    st.title(title)
    st.caption("Accuracy improvement and learning analytics. Audit/paper mode only; no live trading is enabled.")
    selected_day = st.date_input("Review date", value=date.today(), key=key)
    if st.button("Refresh learning data", key=f"{key}_refresh"):
        load_learning_payload.clear()
    payload = load_learning_payload(selected_day)
    return selected_day, payload


def show_df(df: pd.DataFrame | None, title: str, *, height: int = 360) -> None:
    section_title(title)
    if not isinstance(df, pd.DataFrame) or df.empty:
        empty_state(f"No rows available for {title}.")
        return
    professional_table(df, height=height)


def show_expectancy_cards(payload: dict) -> None:
    df = payload.get("risk_expectancy")
    if not isinstance(df, pd.DataFrame) or df.empty:
        warning_box("No evaluated recommendation rows yet, so expectancy is not reliable.")
        return
    row = df.iloc[0].to_dict()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Expected Value", f"{row.get('Expected Value %') or '-'}%", "Average evaluated return")
    with c2:
        metric_card("Profit Factor", row.get("Profit Factor") or "-", "Gross win / gross loss")
    with c3:
        metric_card("Max Drawdown", f"{row.get('Max Drawdown %') or '-'}%", "Cumulative return drawdown")
    with c4:
        metric_card("Entry Reached", f"{row.get('Entry Reached Rate %') or '-'}%", "Excludes missing/pending rows")
