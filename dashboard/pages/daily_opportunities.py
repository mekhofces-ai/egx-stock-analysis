from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import RESEARCH_DISCLAIMER
from app.data.egx_symbols import list_active_symbols
from app.database import SessionLocal
from app.intelligence.final_decision_engine import build_final_decision
from app.intelligence.trade_approval import create_trade_approval, set_trade_approval_status
from app.models import FinalStockDecision, MarketDailyEvaluation, NoTradeReason, Opportunity, Stock, TradeApproval
from app.services.market_daily_evaluation import evaluate_daily_market
from dashboard.ui_components import key_value_table, professional_table, section_title, success_box, warning_box


def _rows_to_df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([row.__dict__ for row in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


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


def render() -> None:
    st.title("Daily Best Opportunities")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(
        "This page now shows one latest current decision per stock from final_stock_decisions and links it to the matching opportunities row. "
        "Historical duplicate decision rows are kept in the database but are not shown as today's opportunities."
    )
    c1, c2, c3 = st.columns([1, 1, 2])
    limit = c1.slider("Refresh symbols", 5, 100, 20, key="daily_opp_refresh_limit")
    min_score = c2.slider("Minimum final score", 0, 100, 60, key="daily_opp_min_score")
    latest_date_only = st.checkbox(
        "Use latest available decision date only",
        value=True,
        help="Prevents older high-score rows from mixing into today's opportunity list.",
    )
    if c3.button("Refresh daily decisions"):
        with SessionLocal() as db, st.spinner("Running final decision engine for active symbols..."):
            for symbol in list_active_symbols(db, limit=limit):
                build_final_decision(db, symbol, run_sources=True, persist=True)
            db.commit()
        st.success("Daily decisions refreshed.")
    with SessionLocal() as db:
        raw_rows = db.scalars(
            select(FinalStockDecision)
            .order_by(FinalStockDecision.decision_date.desc(), FinalStockDecision.final_score.desc())
            .limit(5000)
        ).all()
        latest_rows = _latest_by_symbol(raw_rows)
        latest_decision_date = max((row.decision_date.date() for row in latest_rows if row.decision_date), default=None)
        scoped_rows = [
            row for row in latest_rows
            if not latest_date_only or not latest_decision_date or (row.decision_date and row.decision_date.date() == latest_decision_date)
        ]
        rows = [row for row in scoped_rows if float(row.final_score or 0) >= min_score][:250]
        sector_map = {row.symbol: row.sector for row in db.scalars(select(Stock)).all()}
        opportunities = {row.symbol: row for row in db.scalars(select(Opportunity)).all()}
        market_payload = evaluate_daily_market(db, persist=True)
        db.commit()
        market = db.get(MarketDailyEvaluation, market_payload.get("id")) if market_payload.get("id") else None
    if not rows:
        st.info("No final decisions match this score yet. Refresh daily decisions or import more data.")
        return
    df = _rows_to_df(rows)
    df["sector"] = df["symbol"].map(sector_map).fillna("-")
    df["opportunity_score"] = df["symbol"].map(lambda symbol: getattr(opportunities.get(symbol), "final_score", None))
    df["opportunity_signal"] = df["symbol"].map(lambda symbol: getattr(opportunities.get(symbol), "recommendation", None))
    df["opportunity_updated_at"] = df["symbol"].map(lambda symbol: getattr(opportunities.get(symbol), "updated_at", None))
    df["market_regime_today"] = market.market_regime if market else "-"
    df["trade_permission_today"] = market.trade_permission if market else "DATA_INSUFFICIENT"
    df["data_relation_status"] = df["symbol"].map(lambda symbol: "linked_to_opportunity" if symbol in opportunities else "decision_only")
    cairo_today = datetime.now(ZoneInfo("Africa/Cairo")).date()
    df["decision_date_only"] = pd.to_datetime(df["decision_date"], errors="coerce").dt.date
    df["data_age_days"] = df["decision_date_only"].map(lambda value: (cairo_today - value).days if value else None)
    blocked_by_market = market and market.trade_permission in {"WATCH_ONLY", "BUY_BLOCKED", "SELL_ONLY", "NO_TRADING", "DATA_INSUFFICIENT"}
    stale_symbols = set(df.loc[df["data_age_days"].fillna(999) > 0, "symbol"].tolist())
    df["actionable_signal"] = df.apply(
        lambda row: "STALE_DATA"
        if row.get("symbol") in stale_symbols
        else "WATCH_ONLY_MARKET_BLOCKED"
        if blocked_by_market and str(row.get("final_signal") or "").upper() in {"BUY", "STRONG BUY"}
        else row.get("final_signal"),
        axis=1,
    )
    df["risk_reward"] = df.apply(
        lambda row: round(((row.get("take_profit_1") or 0) - (row.get("entry_price") or 0)) / max((row.get("entry_price") or 0) - (row.get("stop_loss") or 0), 0.0001), 2)
        if row.get("entry_price") and row.get("stop_loss") and row.get("take_profit_1")
        else None,
        axis=1,
    )
    cols = [
        "symbol",
        "sector",
        "actionable_signal",
        "final_signal",
        "final_score",
        "opportunity_signal",
        "opportunity_score",
        "technical_score",
        "financial_score",
        "news_score",
        "telegram_score",
        "strategy_score",
        "liquidity_score",
        "market_regime_today",
        "trade_permission_today",
        "entry_price",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "risk_reward",
        "best_analysis_today",
        "risk_level",
        "no_trade_reason",
        "data_relation_status",
        "data_age_days",
        "decision_date",
        "opportunity_updated_at",
    ]
    relation_counts = df["data_relation_status"].value_counts().to_dict()
    c = st.columns(4)
    c[0].metric("Current symbols", len(df))
    c[1].metric("Linked opportunities", int(relation_counts.get("linked_to_opportunity", 0)))
    c[2].metric("Decision only", int(relation_counts.get("decision_only", 0)))
    c[3].metric("Market permission", market.trade_permission if market else "DATA_INSUFFICIENT")
    if latest_decision_date and latest_decision_date < cairo_today:
        warning_box(
            f"Latest final-decision data is from {latest_decision_date}, not today ({cairo_today}). "
            "Run Refresh daily decisions or automation before treating this as today's actionable list."
        )
    if latest_date_only and len(rows) < 5:
        warning_box(
            f"Only {len(rows)} stock(s) match the latest decision date and score filter. "
            "This is safer than mixing stale older rows into the daily list."
        )
    if relation_counts.get("decision_only", 0):
        warning_box("Some rows have final decisions but no matching opportunity-engine row yet. Run Refresh opportunities to align the actionable shortlist.")
    if market and market.trade_permission != "TRADE_ALLOWED":
        warning_box(f"Market filter is {market.trade_permission}; BUY rows should be treated as watch/blocked unless all safety checks pass.")
    professional_table(df[[col for col in cols if col in df.columns]], height=520)

    with st.expander("Data Relationship Check", expanded=False):
        key_value_table(
            {
                "raw_final_decision_rows_loaded": len(raw_rows),
                "unique_latest_symbols": len(latest_rows),
                "latest_decision_date_used": latest_decision_date,
                "latest_date_only_filter": latest_date_only,
                "candidate_rows_after_date_scope": len(scoped_rows),
                "displayed_after_score_filter": len(df),
                "opportunity_rows_available": len(opportunities),
                "market_evaluation_date": getattr(market, "evaluation_date", None),
                "market_regime": getattr(market, "market_regime", None),
                "trade_permission": getattr(market, "trade_permission", None),
            }
        )

    section_title("Approve / Reject / Watch")
    labels = [f"{row.symbol} | {row.final_signal} | {row.final_score or 0:.0f}%" for row in rows]
    selected = st.selectbox("Selected stock", labels)
    action = st.radio("Action", ["Watch", "Approve", "Reject"], horizontal=True)
    if st.button("Save action"):
        index = labels.index(selected)
        row = rows[index]
        with SessionLocal() as db:
            if action == "Approve":
                approval = create_trade_approval(
                    db,
                    {
                        "symbol": row.symbol,
                        "side": "BUY",
                        "entry_price": row.entry_price,
                        "quantity": None,
                        "total_value": None,
                        "final_score": row.final_score,
                        "final_signal": row.final_signal,
                        "reason": row.reason,
                    },
                    requested_by="dashboard",
                )
                db.flush()
                set_trade_approval_status(db, approval.id, "approved", approved_by="dashboard")
                success_box(f"Approved paper trade proposal for {row.symbol}. Real execution remains disabled.")
            elif action == "Reject":
                db.add(
                    NoTradeReason(
                        symbol=row.symbol,
                        final_score=row.final_score,
                        final_signal=row.final_signal,
                        reasons_json=["manual rejection"],
                        reason_text="Rejected manually from Daily Opportunities.",
                    )
                )
                success_box(f"Rejected {row.symbol}.")
            else:
                db.add(
                    TradeApproval(
                        symbol=row.symbol,
                        side="WATCH",
                        final_score=row.final_score,
                        signal=row.final_signal,
                        reason="Marked as watch from Daily Opportunities.",
                        status="watched",
                        requested_by="dashboard",
                    )
                )
                success_box(f"Added {row.symbol} to watch workflow.")
            db.commit()
