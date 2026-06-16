from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.financial.financial_data import import_financial_csv
from app.models import AutomationSetting, PortfolioSetting, Stock, StrategyDefinition
from app.news.news_ingestion import import_news_csv
from app.services.dynamic_settings import get_setting


def _upsert_setting(db, key: str, value: object, value_type: str, description: str) -> None:
    row = db.get(AutomationSetting, key)
    if not row:
        row = AutomationSetting(key=key, value_type=value_type, description=description)
    row.value = str(value).lower() if isinstance(value, bool) else str(value)
    row.value_type = value_type
    row.description = description
    db.add(row)


def render() -> None:
    st.title("Admin Settings")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        portfolio = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
        strategies = db.scalars(select(StrategyDefinition).order_by(StrategyDefinition.strategy_code.asc())).all()
        stocks = db.scalars(select(Stock).order_by(Stock.symbol.asc())).all()
        dynamic = {
            "liquidity_min_score": get_setting(db, "liquidity_min_score", 35.0, "float"),
            "market_regime_buy_penalty": get_setting(db, "market_regime_buy_penalty", 10.0, "float"),
            "sector_strength_weight": get_setting(db, "sector_strength_weight", 5.0, "float"),
            "ui_theme_mode": get_setting(db, "ui_theme_mode", "light", "string"),
            "ui_language_mode": get_setting(db, "ui_language_mode", "en", "string"),
            "telegram_fetch_limit": get_setting(db, "telegram_fetch_limit", 30, "int"),
            "telegram_analyze_images": get_setting(db, "telegram_analyze_images", False, "bool"),
            "telegram_download_media": get_setting(db, "telegram_download_media", False, "bool"),
            "alert_confidence_threshold": get_setting(db, "alert_confidence_threshold", 70.0, "float"),
        }
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Weights", "Portfolio Safety", "Strategies/Symbols", "Data Uploads", "Risk/Quality & UI"])
    with tab1.form("final_weights_form"):
        c = st.columns(5)
        technical = c[0].number_input("Technical %", 0.0, 100.0, 35.0)
        financial = c[1].number_input("Financial %", 0.0, 100.0, 25.0)
        news = c[2].number_input("News %", 0.0, 100.0, 20.0)
        telegram = c[3].number_input("Telegram %", 0.0, 100.0, 10.0)
        strategy = c[4].number_input("Strategy %", 0.0, 100.0, 10.0)
        if st.form_submit_button("Save final weights"):
            with SessionLocal() as db:
                _upsert_setting(db, "final_weight_technical", technical, "float", "Final decision weight for technical analysis.")
                _upsert_setting(db, "final_weight_financial", financial, "float", "Final decision weight for financial analysis.")
                _upsert_setting(db, "final_weight_news", news, "float", "Final decision weight for news analysis.")
                _upsert_setting(db, "final_weight_telegram", telegram, "float", "Final decision weight for Telegram analysis.")
                _upsert_setting(db, "final_weight_strategy", strategy, "float", "Final decision weight for strategy analysis.")
                db.commit()
            st.success("Weights saved.")
    with tab2.form("portfolio_safety_form"):
        enabled = st.checkbox("Enable portfolio bot", value=bool(portfolio.portfolio_bot_enabled))
        initial_cash = st.number_input("Initial cash", min_value=0.0, value=float(portfolio.initial_cash))
        current_cash = st.number_input("Current cash", min_value=0.0, value=float(portfolio.current_cash))
        max_risk = st.number_input("Max risk per trade %", 0.1, 20.0, float(portfolio.max_risk_per_trade_pct))
        max_position = st.number_input("Max position size %", 1.0, 100.0, float(portfolio.max_position_size_pct))
        max_open = st.number_input("Max open positions", 1, 100, int(portfolio.max_open_positions))
        max_daily_loss = st.number_input("Max daily loss %", 0.1, 50.0, float(portfolio.max_daily_loss_pct))
        max_weekly_loss = st.number_input("Max weekly loss %", 0.1, 50.0, float(portfolio.max_weekly_loss_pct))
        max_drawdown = st.number_input("Max drawdown %", 0.1, 80.0, float(portfolio.max_drawdown_pct))
        max_consecutive_losses = st.number_input("Max consecutive losses", 1, 20, int(portfolio.max_consecutive_losses))
        manual_buy = st.checkbox("Require manual buy confirmation", value=bool(portfolio.require_manual_buy_confirmation))
        manual_sell = st.checkbox("Require manual sell confirmation", value=bool(portfolio.require_manual_sell_confirmation))
        allow_high = st.checkbox("Allow high risk trades", value=bool(portfolio.allow_high_risk_trades))
        buy_threshold = st.number_input("Minimum score to buy", 0.0, 100.0, float(portfolio.minimum_final_score_to_buy))
        sell_threshold = st.number_input("Minimum score to hold", 0.0, 100.0, float(portfolio.minimum_score_to_hold))
        if st.form_submit_button("Save portfolio settings"):
            with SessionLocal() as db:
                row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
                row.portfolio_bot_enabled = enabled
                row.initial_cash = initial_cash
                row.current_cash = current_cash
                row.max_risk_per_trade_pct = max_risk
                row.max_position_size_pct = max_position
                row.max_open_positions = int(max_open)
                row.max_daily_loss_pct = max_daily_loss
                row.max_weekly_loss_pct = max_weekly_loss
                row.max_drawdown_pct = max_drawdown
                row.max_consecutive_losses = int(max_consecutive_losses)
                row.require_manual_buy_confirmation = manual_buy
                row.require_manual_sell_confirmation = manual_sell
                row.allow_high_risk_trades = allow_high
                row.minimum_final_score_to_buy = buy_threshold
                row.minimum_score_to_hold = sell_threshold
                row.trading_mode = "paper_trading"
                row.timezone = "Africa/Cairo"
                db.add(row)
                db.commit()
            st.success("Portfolio safety settings saved.")
    with tab3:
        st.subheader("Strategies")
        sdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in strategies])
        st.dataframe(sdf, use_container_width=True, hide_index=True)
        st.subheader("Symbols")
        stock_df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in stocks])
        st.dataframe(stock_df[["symbol", "name", "name_en", "sector", "is_active"] if "name" in stock_df.columns else stock_df.columns], use_container_width=True, hide_index=True)
    with tab4:
        st.subheader("Financial CSV")
        financial_file = st.file_uploader("Upload financial data CSV", type=["csv"], key="admin_financial_csv")
        if financial_file and st.button("Import financial data"):
            with NamedTemporaryFile(delete=False, suffix=".csv") as handle:
                handle.write(financial_file.getvalue())
                temp_path = handle.name
            with SessionLocal() as db:
                count = import_financial_csv(db, temp_path)
                db.commit()
            Path(temp_path).unlink(missing_ok=True)
            st.success(f"Imported {count} financial row(s).")
        st.subheader("News CSV")
        news_file = st.file_uploader("Upload news CSV", type=["csv"], key="admin_news_csv")
        if news_file and st.button("Import news data"):
            with NamedTemporaryFile(delete=False, suffix=".csv") as handle:
                handle.write(news_file.getvalue())
                temp_path = handle.name
            with SessionLocal() as db:
                count = import_news_csv(db, temp_path)
                db.commit()
            Path(temp_path).unlink(missing_ok=True)
            st.success(f"Imported {count} news row(s).")
    with tab5.form("risk_quality_ui_form"):
        st.subheader("Risk and Quality Filters")
        liquidity_min = st.number_input("Minimum liquidity score", 0.0, 100.0, float(dynamic["liquidity_min_score"]))
        market_penalty = st.number_input("Bearish market buy penalty", 0.0, 50.0, float(dynamic["market_regime_buy_penalty"]))
        sector_weight = st.number_input("Sector strength adjustment weight", 0.0, 25.0, float(dynamic["sector_strength_weight"]))
        alert_threshold = st.number_input("Alert confidence threshold", 0.0, 100.0, float(dynamic["alert_confidence_threshold"]))
        st.subheader("Telegram Data Controls")
        fetch_limit = st.number_input("Telegram fetch limit", 1, 500, int(dynamic["telegram_fetch_limit"]))
        analyze_images = st.checkbox("Enable Telegram image OCR", value=bool(dynamic["telegram_analyze_images"]))
        download_media = st.checkbox("Enable media download", value=bool(dynamic["telegram_download_media"]))
        st.subheader("Interface")
        theme = st.selectbox("Theme mode", ["light", "dark"], index=1 if str(dynamic["ui_theme_mode"]).lower() == "dark" else 0)
        language = st.selectbox(
            "Language mode",
            ["en", "ar", "both"],
            index=["en", "ar", "both"].index(str(dynamic["ui_language_mode"]).lower()) if str(dynamic["ui_language_mode"]).lower() in {"en", "ar", "both"} else 0,
        )
        if st.form_submit_button("Save risk, Telegram, and UI settings"):
            with SessionLocal() as db:
                _upsert_setting(db, "liquidity_min_score", liquidity_min, "float", "Minimum liquidity score required before allowing BUY decisions.")
                _upsert_setting(db, "market_regime_buy_penalty", market_penalty, "float", "Score penalty applied when market regime is bearish.")
                _upsert_setting(db, "sector_strength_weight", sector_weight, "float", "Small final-score adjustment for sector strength.")
                _upsert_setting(db, "alert_confidence_threshold", alert_threshold, "float", "Minimum alert confidence/score.")
                _upsert_setting(db, "telegram_fetch_limit", int(fetch_limit), "int", "Telegram fetch limit per active source.")
                _upsert_setting(db, "telegram_analyze_images", analyze_images, "bool", "Enable OCR/media image analysis.")
                _upsert_setting(db, "telegram_download_media", download_media, "bool", "Download new Telegram image media.")
                _upsert_setting(db, "ui_theme_mode", theme, "string", "Dashboard theme: light or dark.")
                _upsert_setting(db, "ui_language_mode", language, "string", "Dashboard language mode: en, ar, or both.")
                db.commit()
            st.success("Risk, Telegram, and UI settings saved.")
