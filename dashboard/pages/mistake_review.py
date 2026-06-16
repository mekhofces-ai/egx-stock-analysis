from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.review_engine import build_mistake_reviews
from app.models import MistakeReview, PortfolioTrade
from dashboard.ui_components import empty_state, professional_table, section_title


def render() -> None:
    st.title("Mistake Review")
    st.caption(RESEARCH_DISCLAIMER)
    if st.button("Analyze losing trades"):
        with SessionLocal() as db:
            result = build_mistake_reviews(db)
            db.commit()
        st.success(f"Created {result['created']} new mistake review row(s).")
    with SessionLocal() as db:
        reviews = db.scalars(select(MistakeReview).order_by(MistakeReview.created_at.desc()).limit(300)).all()
        losing = db.scalars(select(PortfolioTrade).where(PortfolioTrade.trade_type == "SELL", PortfolioTrade.profit_loss < 0).order_by(PortfolioTrade.trade_date.desc()).limit(300)).all()
    section_title("Losing Trades")
    ldf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in losing])
    professional_table(ldf[["symbol", "trade_date", "price", "quantity", "profit_loss", "profit_loss_pct", "reason"]] if not ldf.empty else ldf)
    section_title("Review Notes")
    rdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in reviews])
    if rdf.empty:
        empty_state("No losing trade reviews yet.")
    else:
        professional_table(rdf[["symbol", "loss_amount", "loss_pct", "suspected_reason", "improvement", "created_at"]])

