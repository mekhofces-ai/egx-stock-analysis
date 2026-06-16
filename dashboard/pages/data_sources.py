from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from app.config import RESEARCH_DISCLAIMER, get_settings
from app.database import SessionLocal
from app.intelligence.final_decision_engine import build_final_decision
from app.models import FinancialData, MarketPrice, NewsSignal, OHLCVData, StockNews
from app.services.dynamic_data_refresh import run_dynamic_data_refresh, select_dynamic_symbols
from app.services.dynamic_settings import get_setting, seed_dynamic_settings, set_setting
from dashboard.ui_components import data_gap_box, key_value_table, professional_table, section_title, success_box


def _counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            "financial_data": db.scalar(select(func.count()).select_from(FinancialData)) or 0,
            "stock_news": db.scalar(select(func.count()).select_from(StockNews)) or 0,
            "ohlcv_data": db.scalar(select(func.count()).select_from(OHLCVData)) or 0,
            "market_prices": db.scalar(select(func.count()).select_from(MarketPrice)) or 0,
            "news_signals": db.scalar(select(func.count()).select_from(NewsSignal)) or 0,
        }


def _result_rows(result: dict) -> pd.DataFrame:
    rows = []
    for source, payload in (result or {}).items():
        if not isinstance(payload, dict):
            rows.append({"source": source, "metric": "result", "value": str(payload)})
            continue
        for key, value in payload.items():
            if key == "errors":
                rows.append({"source": source, "metric": "errors", "value": "; ".join(str(item) for item in (value or [])[:5]) or "-"})
            elif isinstance(value, (dict, list, tuple, set)):
                rows.append({"source": source, "metric": key, "value": str(value)[:500]})
            else:
                rows.append({"source": source, "metric": key, "value": value})
    return pd.DataFrame(rows)


def render() -> None:
    st.title("Dynamic Data Sources")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(
        "These controls fetch real external data only. Financial snapshots use TradingView's Egypt scanner, "
        "news uses free Google News RSS, and OHLCV uses the TradingView chart feed. If a source fails, the app stores the error instead of fake rows."
    )

    counts = _counts()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Financial rows", counts["financial_data"])
    c2.metric("News rows", counts["stock_news"])
    c3.metric("Daily OHLCV", counts["ohlcv_data"])
    c4.metric("Market candles", counts["market_prices"])
    c5.metric("News signals", counts["news_signals"])

    if counts["financial_data"] == 0 or counts["stock_news"] == 0 or counts["ohlcv_data"] == 0:
        data_gap_box(
            "Some real-source tables are still empty",
            "Run the refresh buttons below. TradingView chart history can be slower than screening and may fail for some symbols during network throttling.",
        )

    settings = get_settings()
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        current_limit = int(get_setting(db, "dynamic_data_symbol_limit", 5, "int") or 5)
        current_timeframes = str(get_setting(db, "dynamic_data_timeframes", "1d,1h", "string") or "1d,1h")
        current_news_items = int(get_setting(db, "news_rss_max_items_per_symbol", 5, "int") or 5)
        sample_symbols = select_dynamic_symbols(db, limit=current_limit)

    section_title("Refresh Settings")
    left, mid, right = st.columns(3)
    limit = left.number_input("Symbols per refresh", min_value=1, max_value=300, value=current_limit, step=1)
    timeframes = mid.text_input("OHLCV timeframes", value=current_timeframes, help="Examples: 1d,1h,4h,15m")
    news_items = right.number_input("News items per symbol", min_value=1, max_value=20, value=current_news_items, step=1)
    if st.button("Save refresh settings"):
        with SessionLocal() as db:
            set_setting(db, "dynamic_data_symbol_limit", int(limit), value_type="int")
            set_setting(db, "dynamic_data_timeframes", timeframes, value_type="string")
            set_setting(db, "news_rss_max_items_per_symbol", int(news_items), value_type="int")
        success_box("Dynamic data settings saved.")

    st.caption("Current automatic symbol selection starts with top opportunities, then active stocks.")
    professional_table(pd.DataFrame({"selected_symbols": sample_symbols}), height=180)

    section_title("Manual Refresh")
    run_cols = st.columns(4)
    run_financial = run_cols[0].button("Fetch financials")
    run_news = run_cols[1].button("Fetch news")
    run_ohlcv = run_cols[2].button("Fetch OHLCV")
    run_all = run_cols[3].button("Run all sources", type="primary")

    refresh_decisions = st.checkbox("Refresh final decisions for refreshed symbols after import", value=True)
    if run_financial or run_news or run_ohlcv or run_all:
        selected_timeframes = [part.strip() for part in timeframes.split(",") if part.strip()]
        with st.spinner("Refreshing dynamic data sources..."):
            with SessionLocal() as db:
                result = run_dynamic_data_refresh(
                    db,
                    settings=settings,
                    limit=int(limit),
                    refresh_financial=run_all or run_financial,
                    refresh_news=run_all or run_news,
                    refresh_ohlcv=run_all or run_ohlcv,
                    timeframes=selected_timeframes,
                )
                refreshed_symbols = select_dynamic_symbols(db, limit=int(limit))
                if refresh_decisions:
                    for symbol in refreshed_symbols:
                        build_final_decision(db, symbol, run_sources=True, persist=True)
                    db.commit()
        success_box("Dynamic refresh finished.")
        professional_table(_result_rows(result), height=360)

    section_title("Automation")
    key_value_table(
        {
            "financial_source": "TradingView Egypt scanner, no API key",
            "news_source": "Google News RSS, no API key",
            "ohlcv_source": "TradingView chart websocket, no API key but best effort",
            "automation_financial_interval": "financial_refresh_interval_seconds",
            "automation_news_interval": "news_refresh_interval_seconds",
            "automation_ohlcv_interval": "ohlcv_refresh_interval_seconds",
        }
    )
