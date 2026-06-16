from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover - optional dashboard dependency
    go = None

from app.config import RESEARCH_DISCLAIMER
from app.data.egx_symbols import list_active_symbols
from app.data.market_data import get_ohlcv
from app.database import SessionLocal
from app.intelligence.final_decision_engine import build_final_decision, latest_final_decision
from app.intelligence.review_engine import scenario_for_stock
from app.models import (
    FinancialSignal,
    NewsSignal,
    NoTradeReason,
    SignalAccuracyTracking,
    StrategySignal,
    TelegramSignal,
    TechnicalSignal,
)
from dashboard.ui_components import data_gap_box, empty_state, key_value_table, professional_table, risk_badge, score_badge, section_title, signal_badge


def _row_dict(row: object | None) -> dict:
    if not row:
        return {}
    return {key: value for key, value in row.__dict__.items() if not key.startswith("_")}


def _render_signal_record(record: object | None, fields: list[str], empty_message: str) -> None:
    payload = _row_dict(record)
    if not payload:
        empty_state(empty_message)
        return
    data = {field: payload.get(field) for field in fields if field in payload}
    key_value_table(data)


def _render_quality_panel(quality: dict) -> None:
    rows: list[dict[str, object]] = []
    market = quality.get("market") or {}
    sector = quality.get("sector") or {}
    liquidity = quality.get("liquidity") or {}
    if market:
        rows.append({"Area": "Market regime", "Status": market.get("regime"), "Score": market.get("market_score"), "Reason": market.get("reason")})
    if sector:
        rows.append({"Area": "Sector strength", "Status": sector.get("status"), "Score": sector.get("relative_score"), "Reason": sector.get("reason")})
    if liquidity:
        rows.append({"Area": "Liquidity", "Status": liquidity.get("status"), "Score": liquidity.get("liquidity_score"), "Reason": liquidity.get("reason")})
    if rows:
        professional_table(pd.DataFrame(rows))
    else:
        empty_state("No risk-quality details are stored for this decision yet.")


def render() -> None:
    st.title("Stock Full Analysis")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        symbols = list_active_symbols(db)
    symbol = st.selectbox("Symbol", symbols or ["COMI"], key="stock_full_symbol")
    if st.button("Refresh full analysis"):
        with SessionLocal() as db, st.spinner(f"Running analysis for {symbol}..."):
            result = build_final_decision(db, symbol, run_sources=True, persist=True)
            db.commit()
        st.success(f"{symbol}: {result['final_signal']} at {result['final_score']:.0f}%")
    with SessionLocal() as db:
        decision = latest_final_decision(db, symbol)
        candles = get_ohlcv(db, symbol, limit=260)
        technical = db.scalar(select(TechnicalSignal).where(TechnicalSignal.symbol == symbol).order_by(TechnicalSignal.signal_date.desc()))
        financial = db.scalar(select(FinancialSignal).where(FinancialSignal.symbol == symbol).order_by(FinancialSignal.signal_date.desc()))
        news = db.scalar(select(NewsSignal).where(NewsSignal.symbol == symbol).order_by(NewsSignal.signal_date.desc()))
        telegram = db.scalar(select(TelegramSignal).where(TelegramSignal.symbol == symbol).order_by(TelegramSignal.signal_date.desc()))
        strategies = db.scalars(select(StrategySignal).where(StrategySignal.symbol == symbol).order_by(StrategySignal.signal_date.desc()).limit(12)).all()
        accuracy = db.scalars(select(SignalAccuracyTracking).where(SignalAccuracyTracking.symbol == symbol).order_by(SignalAccuracyTracking.updated_at.desc()).limit(20)).all()
        no_trade = db.scalars(select(NoTradeReason).where(NoTradeReason.symbol == symbol).order_by(NoTradeReason.created_at.desc()).limit(10)).all()
    if candles.empty:
        data_gap_box(
            "No OHLCV candles found",
            "The decision engine has screener, Telegram, report, and strategy rows, but this symbol has no stored historical candles.",
            "Import OHLCV CSV or enable a TradingView chart data source before expecting candlestick charts/backtests here.",
        )
    elif go is not None:
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=candles["datetime"],
                    open=candles["open"],
                    high=candles["high"],
                    low=candles["low"],
                    close=candles["close"],
                    name=symbol,
                    increasing_line_color="#16a34a",
                    decreasing_line_color="#dc2626",
                )
            ]
        )
        fig.update_layout(
            height=460,
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False,
            template="plotly_white",
            yaxis_title="Price",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        chart_df = candles[["datetime", "close"]].set_index("datetime")
        st.line_chart(chart_df, use_container_width=True)
    if decision:
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(signal_badge(decision.final_signal), unsafe_allow_html=True)
        c1.caption("Final signal")
        c2.markdown(score_badge(decision.final_score), unsafe_allow_html=True)
        c2.caption("Final score")
        c3.metric("Best source", decision.best_analysis_today or "-")
        c4.markdown(risk_badge(decision.risk_level), unsafe_allow_html=True)
        c4.caption("Risk")
        st.write(decision.reason)
        if decision.no_trade_reason:
            st.warning(f"No-trade / caution: {decision.no_trade_reason}")
    tabs = st.tabs(["Summary", "Technical", "Financial", "News", "Telegram", "Strategy", "Accuracy", "Scenario"])
    with tabs[0]:
        if decision:
            summary_cols = st.columns(4)
            summary_cols[0].metric("Entry", f"{decision.entry_price or 0:.2f}")
            summary_cols[1].metric("Stop loss", f"{decision.stop_loss or 0:.2f}")
            summary_cols[2].metric("TP1", f"{decision.take_profit_1 or 0:.2f}")
            summary_cols[3].metric("TP2", f"{decision.take_profit_2 or 0:.2f}")
            components = decision.components_json or {}
            scores = {
                "Technical": decision.technical_score,
                "Financial": decision.financial_score,
                "News": decision.news_score,
                "Telegram": decision.telegram_score,
                "Strategy": decision.strategy_score,
                "Liquidity": decision.liquidity_score,
                "Sector": decision.sector_score,
            }
            score_df = pd.DataFrame(
                [{"source": key, "score": value} for key, value in scores.items() if value is not None]
            )
            if not score_df.empty:
                st.bar_chart(score_df.set_index("source"))
            if components.get("risk_quality"):
                section_title("Risk And Quality Checks")
                _render_quality_panel(components["risk_quality"])
        else:
            empty_state("No final decision exists for this symbol yet. Press Refresh full analysis to create one.")
        reason_df = pd.DataFrame([_row_dict(row) for row in no_trade])
        section_title("No-Trade Reasons")
        if reason_df.empty:
            empty_state("No recent no-trade reasons stored for this symbol.")
        else:
            professional_table(reason_df[["final_signal", "final_score", "reason_text", "created_at"]])
    with tabs[1]:
        _render_signal_record(
            technical,
            ["signal", "technical_score", "entry_price", "stop_loss", "take_profit_1", "take_profit_2", "confidence", "risk_level", "reason", "signal_date"],
            "No technical signal yet.",
        )
    with tabs[2]:
        _render_signal_record(
            financial,
            ["financial_signal", "financial_score", "profitability_score", "growth_score", "valuation_score", "debt_score", "cashflow_score", "risk_level", "reason", "signal_date"],
            "No financial signal yet. Upload financial CSV data from Admin Settings or Imports.",
        )
    with tabs[3]:
        _render_signal_record(
            news,
            ["news_signal", "news_score", "main_news_drivers", "reason", "signal_date"],
            "No news signal yet. Import news CSV or add a news ingestion source.",
        )
    with tabs[4]:
        _render_signal_record(
            telegram,
            ["telegram_signal", "telegram_score", "top_channels", "reason", "signal_date"],
            "No Telegram signal yet.",
        )
    strategy_df = pd.DataFrame([_row_dict(row) for row in strategies])
    tabs[5].dataframe(strategy_df, use_container_width=True, hide_index=True) if not strategy_df.empty else tabs[5].info("No modular strategy signals yet.")
    with tabs[6]:
        accuracy_df = pd.DataFrame([_row_dict(row) for row in accuracy])
        if accuracy_df.empty:
            empty_state("No accuracy rows yet. Accuracy needs later prices after a signal is created.")
        else:
            visible = [
                "decision_date",
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
            professional_table(accuracy_df[[col for col in visible if col in accuracy_df.columns]])
    with tabs[7]:
        capital = st.number_input("Capital to simulate (EGP)", min_value=100.0, value=10000.0, step=500.0)
        if st.button("Run scenario"):
            with SessionLocal() as db:
                scenario = scenario_for_stock(db, symbol, capital)
            if scenario.get("status") != "ok":
                st.warning(scenario.get("reason", "Scenario could not be calculated."))
            else:
                c = st.columns(5)
                c[0].metric("Quantity", scenario["quantity"])
                c[1].metric("Entry", f"{scenario['entry_price']:.2f}")
                c[2].metric("Expected loss", f"{scenario['expected_loss']:,.2f} EGP")
                c[3].metric("Expected gain TP1", f"{scenario['expected_gain_1']:,.2f} EGP")
                c[4].metric("Risk/Reward", scenario["risk_reward_ratio"] or "-")
                key_value_table(scenario)
