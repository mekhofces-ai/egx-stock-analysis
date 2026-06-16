from __future__ import annotations

import pandas as pd
import streamlit as st
from datetime import datetime
from sqlalchemy import select
from zoneinfo import ZoneInfo

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.portfolio_bot import get_portfolio_settings, portfolio_value
from app.models import (
    DailyEGXReportRow,
    FinalStockDecision,
    FinancialData,
    MarketPrice,
    NoTradeReason,
    OHLCVData,
    Opportunity,
    PortfolioPosition,
    PortfolioTrade,
    StockCombinedAnalysis,
    StockNews,
    TelegramMessage,
)
from app.services.market_daily_evaluation import evaluate_daily_market
from dashboard.ui_components import data_gap_box, key_value_table, metric_card, professional_table, section_title, signal_badge, warning_box


CAIRO_TZ = ZoneInfo("Africa/Cairo")


def _latest_by_symbol(rows: list[FinalStockDecision]) -> list[FinalStockDecision]:
    latest: dict[str, FinalStockDecision] = {}
    for row in rows:
        symbol = str(row.symbol or "").upper()
        if not symbol:
            continue
        current = latest.get(symbol)
        current_key = (current.decision_date, current.id) if current else None
        row_key = (row.decision_date, row.id)
        if current is None or row_key > current_key:
            latest[symbol] = row
    return sorted(latest.values(), key=lambda item: (float(item.final_score or 0), item.decision_date), reverse=True)


def _date_age_days(value: object, today: object) -> int | None:
    if value is None:
        return None
    try:
        item_date = pd.to_datetime(value).date()
        return (today - item_date).days
    except Exception:
        return None


def _freshness_label(latest_date: object, today: object) -> str:
    if latest_date is None:
        return "No data"
    age = _date_age_days(latest_date, today)
    if age == 0:
        return "Fresh today"
    return f"Stale by {age} day(s)"


def render() -> None:
    st.title("Executive Overview")
    st.caption(RESEARCH_DISCLAIMER)
    cairo_today = datetime.now(CAIRO_TZ).date()
    with SessionLocal() as db:
        settings = get_portfolio_settings(db)
        values = portfolio_value(db, settings)
        market = evaluate_daily_market(db, persist=True)
        db.commit()
        positions = db.query(PortfolioPosition).filter(PortfolioPosition.status == "open").count()
        trades = db.scalars(
            select(PortfolioTrade)
            .where(PortfolioTrade.trade_type == "SELL")
            .order_by(PortfolioTrade.trade_date.desc())
            .limit(200)
        ).all()
        today_sells = [row for row in trades if row.trade_date and row.trade_date.date() == cairo_today]
        today_realized_pnl = sum(float(row.profit_loss or 0) for row in today_sells)
        raw_decisions = db.scalars(
            select(FinalStockDecision)
            .order_by(FinalStockDecision.decision_date.desc(), FinalStockDecision.final_score.desc())
            .limit(5000)
        ).all()
        latest_by_symbol = _latest_by_symbol(raw_decisions)
        latest_decision_date = max((row.decision_date.date() for row in latest_by_symbol if row.decision_date), default=None)
        latest = [row for row in latest_by_symbol if latest_decision_date and row.decision_date and row.decision_date.date() == latest_decision_date]
        top = latest[0] if latest else None
        risk = next((row for row in latest if str(row.risk_level or "").upper() == "HIGH"), None)
        no_trade = db.scalars(select(NoTradeReason).order_by(NoTradeReason.created_at.desc()).limit(10)).all()
        latest_market_price = db.scalar(select(MarketPrice.timestamp).order_by(MarketPrice.timestamp.desc()))
        latest_ohlcv = db.scalar(select(OHLCVData.datetime).order_by(OHLCVData.datetime.desc()))
        latest_opportunity = db.scalar(select(Opportunity.updated_at).order_by(Opportunity.updated_at.desc()))
        coverage = {
            "Historical OHLCV": db.query(OHLCVData).count() + db.query(MarketPrice).count(),
            "Daily EGX Excel rows": db.query(DailyEGXReportRow).count(),
            "Financial raw rows": db.query(FinancialData).count(),
            "News raw rows": db.query(StockNews).count(),
            "Telegram messages": db.query(TelegramMessage).count(),
            "Opportunities": db.query(Opportunity).count(),
            "Combined analysis": db.query(StockCombinedAnalysis).count(),
            "Final decisions": db.query(FinalStockDecision).count(),
            "Paper trades": db.query(PortfolioTrade).count(),
        }
    win_rate = 0.0
    if trades:
        win_rate = sum(1 for row in trades if (row.profit_loss or 0) > 0) / len(trades) * 100
    cols = st.columns(6)
    with cols[0]:
        metric_card("Portfolio Value", f"{values['total_value']:,.0f}", "EGP")
    with cols[1]:
        metric_card("Today Realized P/L", f"{today_realized_pnl:,.0f}", f"{len(today_sells)} sell trade(s)")
    with cols[2]:
        metric_card("Open Positions", positions)
    with cols[3]:
        metric_card("Win Rate", f"{win_rate:.0f}%")
    with cols[4]:
        metric_card("Market Regime", str(market.get("market_regime") or "unknown").replace("_", " ").title(), f"Score {market.get('market_score') or '-'}")
    with cols[5]:
        metric_card(
            "Top Setup",
            top.symbol if top and latest_decision_date == cairo_today else "No fresh setup",
            f"{top.final_score:.0f}% | {latest_decision_date}" if top and top.final_score is not None else _freshness_label(latest_decision_date, cairo_today),
        )

    if latest_decision_date != cairo_today:
        warning_box(
            f"Executive data is not fresh for today. Latest final decisions are from {latest_decision_date}; "
            f"today is {cairo_today}. Run analysis/automation before using this page for today's decisions."
        )
    if market.get("trade_permission") != "TRADE_ALLOWED":
        warning_box(
            f"Market daily evaluation is {market.get('trade_permission')}. BUY/STRONG BUY setups should be treated as watch or blocked until conditions improve."
        )

    section_title("Data Freshness")
    key_value_table(
        {
            "today_cairo": cairo_today,
            "latest_final_decision_date": latest_decision_date,
            "final_decision_freshness": _freshness_label(latest_decision_date, cairo_today),
            "latest_market_price_timestamp": latest_market_price,
            "market_price_freshness": _freshness_label(latest_market_price, cairo_today),
            "latest_ohlcv_timestamp": latest_ohlcv,
            "ohlcv_freshness": _freshness_label(latest_ohlcv, cairo_today),
            "latest_opportunity_update": latest_opportunity,
            "opportunity_freshness": _freshness_label(latest_opportunity, cairo_today),
            "market_permission": market.get("trade_permission"),
        }
    )

    section_title("Executive Signals")
    c1, c2, c3 = st.columns(3)
    top_signal = top.final_signal if top and latest_decision_date == cairo_today else "STALE_DATA"
    c1.markdown(f"Top setup from latest data: {top.symbol if top else '-'} {signal_badge(top_signal if top else None)}", unsafe_allow_html=True)
    c2.markdown(f"Highest risk stock: {risk.symbol if risk else '-'} {signal_badge(risk.final_signal if risk else None)}", unsafe_allow_html=True)
    best_driver = "-"
    if latest:
        counts = pd.Series([row.best_analysis_today for row in latest if row.best_analysis_today]).value_counts()
        best_driver = counts.index[0] if not counts.empty else "-"
    c3.metric("Best analysis type today", best_driver)

    section_title("Latest Decisions")
    df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in latest])
    if not df.empty:
        df["data_age_days"] = pd.to_datetime(df["decision_date"], errors="coerce").dt.date.map(lambda value: (cairo_today - value).days if value else None)
        df["actionable_signal"] = df.apply(
            lambda row: "STALE_DATA"
            if row.get("data_age_days") and row.get("data_age_days") > 0
            else "WATCH_ONLY_MARKET_BLOCKED"
            if market.get("trade_permission") != "TRADE_ALLOWED" and str(row.get("final_signal") or "").upper() in {"BUY", "STRONG BUY"}
            else row.get("final_signal"),
            axis=1,
        )
    visible = [
        "symbol",
        "actionable_signal",
        "final_signal",
        "final_score",
        "market_regime",
        "liquidity_score",
        "sector_score",
        "best_analysis_today",
        "risk_level",
        "no_trade_reason",
        "data_age_days",
        "decision_date",
    ]
    professional_table(df[[col for col in visible if col in df.columns]] if not df.empty else df)
    section_title("Recent No-Trade Reasons")
    nt = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in no_trade])
    professional_table(nt[["symbol", "final_signal", "final_score", "reason_text", "created_at"]] if not nt.empty else nt)

    section_title("Data Coverage")
    coverage_rows = []
    next_actions = {
        "Historical OHLCV": "Import OHLCV CSV or connect a historical TradingView chart feed.",
        "Financial raw rows": "Upload financial CSV from Admin Settings.",
        "News raw rows": "Import news CSV or add a news source.",
        "Paper trades": "Run portfolio scan, approve a paper trade, then execute it.",
    }
    for source, count in coverage.items():
        coverage_rows.append(
            {
                "Source": source,
                "Rows": count,
                "Status": "Ready" if count else "No data yet",
                "Next Action": next_actions.get(source, "Automation/import already produced data."),
            }
        )
    coverage_df = pd.DataFrame(coverage_rows)
    professional_table(coverage_df)
    missing = coverage_df[coverage_df["Rows"] == 0]["Source"].tolist()
    if missing:
        data_gap_box(
            "Some pages have no data because their source tables are empty",
            "Empty sources: " + ", ".join(missing) + ". Existing analysis continues using the sources that are available.",
        )
