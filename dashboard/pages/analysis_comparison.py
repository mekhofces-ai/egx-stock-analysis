from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.review_engine import best_analysis_by_symbol
from app.models import FinalStockDecision, SignalAccuracyTracking
from dashboard.ui_components import empty_state, professional_table, section_title


def render() -> None:
    st.title("Analysis Comparison")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        decisions = db.scalars(select(FinalStockDecision).order_by(FinalStockDecision.decision_date.desc()).limit(500)).all()
        accuracy = db.scalars(select(SignalAccuracyTracking).order_by(SignalAccuracyTracking.updated_at.desc()).limit(500)).all()
        best_rows = pd.DataFrame(best_analysis_by_symbol(db))
    decision_df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in decisions])
    accuracy_df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in accuracy])
    if decision_df.empty:
        empty_state("No final stock decisions yet.")
        return
    if not best_rows.empty:
        decision_df = decision_df.merge(best_rows[["symbol", "best_historical_analysis"]], on="symbol", how="left")
    cols = [
        "symbol",
        "technical_score",
        "financial_score",
        "news_score",
        "telegram_score",
        "strategy_score",
        "liquidity_score",
        "sector_score",
        "final_score",
        "final_signal",
        "best_analysis_today",
        "best_historical_analysis",
        "market_regime",
        "risk_level",
        "no_trade_reason",
        "decision_date",
    ]
    section_title("Professional Comparison Matrix")
    professional_table(decision_df[[col for col in cols if col in decision_df.columns]], height=420)
    if not accuracy_df.empty:
        section_title("Actual Movement And Correctness")
        visible = [
            "symbol",
            "move_1d_pct",
            "move_3d_pct",
            "move_5d_pct",
            "technical_correct",
            "financial_correct",
            "news_correct",
            "telegram_correct",
            "strategy_correct",
            "final_decision_correct",
            "actual_best_driver",
        ]
        professional_table(accuracy_df[[col for col in visible if col in accuracy_df.columns]], height=360)
    else:
        empty_state("No accuracy rows yet. The comparison will get richer after later OHLCV prices are available.")
    if not best_rows.empty:
        section_title("Best Analysis Per Stock")
        professional_table(best_rows, height=320)
