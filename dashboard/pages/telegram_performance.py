from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import ChannelPerformance, TelegramChannelPerformance
from app.services.performance_tracker import update_channel_performance
from app.telegram.channel_performance import update_telegram_channel_performance


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])


def render() -> None:
    st.title("Telegram Channel Performance")
    st.caption(RESEARCH_DISCLAIMER)
    c1, c2 = st.columns(2)
    if c1.button("Update legacy channel performance"):
        with SessionLocal() as db:
            update_channel_performance(db)
        st.success("Legacy performance refreshed.")
    if c2.button("Update symbol channel performance"):
        with SessionLocal() as db:
            rows = update_telegram_channel_performance(db)
            db.commit()
        st.success(f"Updated {len(rows)} channel-symbol rows.")
    with SessionLocal() as db:
        symbol_rows = db.scalars(select(TelegramChannelPerformance).order_by(TelegramChannelPerformance.updated_at.desc()).limit(1000)).all()
        legacy_rows = db.scalars(select(ChannelPerformance).order_by(ChannelPerformance.updated_at.desc()).limit(200)).all()
    tab1, tab2 = st.tabs(["By Symbol", "Legacy Source Summary"])
    sdf = _df(symbol_rows)
    tab1.dataframe(sdf, use_container_width=True, hide_index=True) if not sdf.empty else tab1.info("No symbol performance rows yet.")
    ldf = _df(legacy_rows)
    tab2.dataframe(ldf, use_container_width=True, hide_index=True) if not ldf.empty else tab2.info("No legacy performance rows yet.")

