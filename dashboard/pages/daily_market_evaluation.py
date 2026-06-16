from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import MarketDailyEvaluation
from app.services.market_daily_evaluation import evaluate_daily_market, latest_market_evaluation
from dashboard.ui_components import key_value_table, professional_table, section_title, success_box, warning_box


def render() -> None:
    st.title("Daily Market Evaluation")
    st.caption("Market permission gate for recommendations and live trading. " + RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        current = latest_market_evaluation(db) or evaluate_daily_market(db, persist=True)
        db.commit()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Market Score", f"{current.get('market_score') or 0:.0f}/100")
    c2.metric("Regime", current.get("market_regime") or "-")
    c3.metric("Permission", current.get("trade_permission") or "-")
    c4.metric("Status", current.get("market_status") or "-")
    if current.get("trade_permission") == "TRADE_ALLOWED":
        success_box("Market evaluation allows trading, subject to all live-trading safety checks.")
    else:
        warning_box("Market evaluation blocks or limits new BUY trades.")
    st.write(current.get("explanation") or "")
    if current.get("warnings"):
        for warning in current["warnings"]:
            warning_box(warning)
    if st.button("Run Market Evaluation Now"):
        with SessionLocal() as db:
            current = evaluate_daily_market(db, persist=True)
            db.commit()
        st.success("Market evaluation refreshed.")
        key_value_table(current)
    section_title("Latest Evaluation")
    key_value_table(current)
    with SessionLocal() as db:
        rows = db.scalars(
            select(MarketDailyEvaluation).order_by(MarketDailyEvaluation.evaluation_date.desc()).limit(30)
        ).all()
    df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])
    professional_table(df, height=360)
