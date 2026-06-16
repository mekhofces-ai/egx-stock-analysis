from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import NewsSignal, Stock, StockNews
from app.news.news_engine import analyze_news
from app.news.news_ingestion import import_news_csv
from dashboard.ui_components import data_gap_box, key_value_table, professional_table, section_title, success_box


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([row.__dict__ for row in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


def render() -> None:
    st.title("News Analysis")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(
        "This page separates real news items from generated news signals. "
        "When stock_news is empty, the news engine returns neutral scores so the final decision can still run without inventing news."
    )

    with SessionLocal() as db:
        raw_count = db.scalar(select(func.count()).select_from(StockNews)) or 0
        signal_count = db.scalar(select(func.count()).select_from(NewsSignal)) or 0
        symbol_count = db.scalar(select(func.count(func.distinct(StockNews.symbol)))) or 0
        latest_news = db.scalar(select(StockNews).order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc()))
        latest_signal = db.scalar(select(NewsSignal).order_by(NewsSignal.signal_date.desc(), NewsSignal.id.desc()))
        symbols = [row.symbol for row in db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all()]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw news items", raw_count)
    c2.metric("Symbols with news", symbol_count)
    c3.metric("News signals", signal_count)
    c4.metric("Latest news", str((latest_news.published_at or latest_news.created_at))[:16] if latest_news else "-")

    if raw_count == 0:
        data_gap_box(
            "No raw stock news is imported yet",
            "News Analysis exists, but there are no real news rows to score. Current final decisions use neutral news scores until you import news.",
            "Upload a CSV below. Useful columns: symbol, title, body or text, source, published_at or date, expected_impact_duration.",
        )

    with st.expander("Upload news CSV", expanded=raw_count == 0):
        st.write("Accepted columns: symbol, ticker, title, body, text, news, source, published_at, date, expected_impact_duration.")
        news_file = st.file_uploader("News CSV", type=["csv"], key="news_analysis_csv")
        if news_file and st.button("Import news CSV", type="primary"):
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
                    handle.write(news_file.getvalue())
                    temp_path = handle.name
                with SessionLocal() as db:
                    imported = import_news_csv(db, temp_path)
                    db.commit()
                success_box(f"Imported {imported} news row(s). Refresh or run news analysis for selected symbols.")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    selected_symbol = st.selectbox("Symbol", symbols or ["COMI"], key="news_symbol")
    if st.button("Run news analysis for selected symbol"):
        with SessionLocal() as db:
            result = analyze_news(db, selected_symbol, persist=True)
            db.commit()
        success_box(f"News analysis refreshed for {selected_symbol}: {result.get('news_signal')} at {float(result.get('news_score') or 0):.0f}%.")

    tabs = st.tabs(["Latest News Signals", "Selected Symbol", "Raw News"])
    with SessionLocal() as db:
        signals_df = _df(
            db.scalars(
                select(NewsSignal)
                .order_by(NewsSignal.signal_date.desc(), NewsSignal.id.desc())
                .limit(500)
            ).all()
        )
        selected_signal = db.scalar(
            select(NewsSignal)
            .where(NewsSignal.symbol == selected_symbol)
            .order_by(NewsSignal.signal_date.desc(), NewsSignal.id.desc())
        )
        selected_news = _df(
            db.scalars(
                select(StockNews)
                .where(StockNews.symbol == selected_symbol)
                .order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc())
                .limit(50)
            ).all()
        )
        raw_news_df = _df(
            db.scalars(
                select(StockNews)
                .order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc())
                .limit(300)
            ).all()
        )

    with tabs[0]:
        visible = ["symbol", "news_signal", "news_score", "main_news_drivers", "reason", "signal_date"]
        professional_table(signals_df[[col for col in visible if col in signals_df.columns]], height=520)

    with tabs[1]:
        section_title(f"{selected_symbol} News Signal")
        if selected_signal:
            key_value_table(
                {
                    "signal": selected_signal.news_signal,
                    "score": f"{float(selected_signal.news_score or 0):.0f}%",
                    "drivers": selected_signal.main_news_drivers,
                    "reason": selected_signal.reason,
                    "last_update": selected_signal.signal_date,
                }
            )
        else:
            data_gap_box("No news signal for this symbol", "Run news analysis after importing data, or let the final decision engine create a neutral fallback.")
        section_title("Raw News For Symbol")
        if selected_news.empty:
            data_gap_box("No raw news for selected symbol", "Import news rows with this stock symbol to score real news impact.")
        else:
            visible = ["symbol", "title", "source", "published_at", "sentiment", "sentiment_score", "impact_score", "expected_impact_duration"]
            professional_table(selected_news[[col for col in visible if col in selected_news.columns]], height=300)

    with tabs[2]:
        if raw_news_df.empty:
            data_gap_box("Raw news table is empty", "Import news CSV data or connect a news source. No fake news rows are created.")
        else:
            visible = ["symbol", "title", "source", "published_at", "sentiment", "sentiment_score", "impact_score", "created_at"]
            professional_table(raw_news_df[[col for col in visible if col in raw_news_df.columns]], height=520)
