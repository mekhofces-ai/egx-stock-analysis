from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import NewsSignal, SignalAccuracyTracking, StockNews


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])


def render() -> None:
    st.title("News Impact")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        news = db.scalars(select(StockNews).order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc()).limit(300)).all()
        signals = db.scalars(select(NewsSignal).order_by(NewsSignal.signal_date.desc()).limit(300)).all()
        accuracy = db.scalars(select(SignalAccuracyTracking).where(SignalAccuracyTracking.news_correct.is_not(None)).order_by(SignalAccuracyTracking.updated_at.desc()).limit(300)).all()
    tabs = st.tabs(["News Items", "News Signals", "Actual Movement"])
    ndf = _df(news)
    tabs[0].dataframe(ndf, use_container_width=True, hide_index=True) if not ndf.empty else tabs[0].info("No stored news yet.")
    sdf = _df(signals)
    tabs[1].dataframe(sdf, use_container_width=True, hide_index=True) if not sdf.empty else tabs[1].info("No news signals yet.")
    adf = _df(accuracy)
    visible = ["symbol", "move_1d_pct", "move_3d_pct", "move_5d_pct", "news_correct", "actual_best_driver", "updated_at"]
    tabs[2].dataframe(adf[[col for col in visible if col in adf.columns]], use_container_width=True, hide_index=True) if not adf.empty else tabs[2].info("No news accuracy rows yet.")

