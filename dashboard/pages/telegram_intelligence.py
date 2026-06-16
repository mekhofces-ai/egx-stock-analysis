from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import TelegramChannelPerformance, TelegramMediaAnalysis, TelegramMessage, TelegramMessageSymbol, TelegramSignal
from dashboard.ui_components import professional_table, section_title


def render() -> None:
    st.title("Telegram Intelligence")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        symbols = db.scalars(select(TelegramMessageSymbol).order_by(TelegramMessageSymbol.created_at.desc()).limit(2000)).all()
        signals = db.scalars(select(TelegramSignal).order_by(TelegramSignal.signal_date.desc()).limit(300)).all()
        channels = db.scalars(select(TelegramChannelPerformance).order_by(TelegramChannelPerformance.win_rate.desc().nullslast()).limit(300)).all()
        messages = db.scalars(select(TelegramMessage).order_by(TelegramMessage.created_at.desc()).limit(300)).all()
        media = db.scalars(select(TelegramMediaAnalysis).order_by(TelegramMediaAnalysis.created_at.desc()).limit(200)).all()
    sym_df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in symbols])
    top_mentions = sym_df["symbol"].value_counts().rename_axis("symbol").reset_index(name="mentions") if not sym_df.empty else pd.DataFrame()
    c1, c2, c3 = st.columns(3)
    c1.metric("Telegram symbol mentions", len(symbols))
    c2.metric("Tracked signals", len(signals))
    c3.metric("Image/OCR rows", len(media))
    tabs = st.tabs(["Top Mentions", "Telegram Signals", "Channel Quality", "Latest Messages", "Images/OCR"])
    with tabs[0]:
        professional_table(top_mentions)
    with tabs[1]:
        sdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in signals])
        professional_table(sdf[["symbol", "telegram_signal", "telegram_score", "top_channels", "reason", "signal_date"]] if not sdf.empty else sdf)
    with tabs[2]:
        cdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in channels])
        professional_table(cdf)
    with tabs[3]:
        mdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in messages])
        visible = ["channel_name", "symbol", "sentiment", "recommendation_type", "message_type", "target_price", "stop_loss", "created_at"]
        professional_table(mdf[[col for col in visible if col in mdf.columns]] if not mdf.empty else mdf)
    with tabs[4]:
        section_title("Image/Chart Messages")
        idf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in media])
        professional_table(idf[["media_type", "status", "detected_symbols", "ocr_text", "created_at"]] if not idf.empty else idf)
