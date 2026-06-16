from __future__ import annotations

import streamlit as st

from app.database import SessionLocal
from app.services.market_daily_evaluation import evaluate_daily_market, latest_market_evaluation
from dashboard.ui_components import key_value_table, metric_card, warning_box, success_box


def render() -> None:
    st.title("Market Regime")
    st.caption("Daily market condition gate used to downgrade weak BUY signals and block live execution.")
    with SessionLocal() as db:
        market = latest_market_evaluation(db) or evaluate_daily_market(db, persist=True)
        db.commit()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Market Score", f"{market.get('market_score') or 0:.0f}/100")
    with c2:
        metric_card("Regime", market.get("market_regime") or "-")
    with c3:
        metric_card("Permission", market.get("trade_permission") or "-")
    with c4:
        metric_card("Status", market.get("market_status") or "-")
    if market.get("trade_permission") == "TRADE_ALLOWED":
        success_box("Market regime allows only validated, risk-controlled ideas. Live trading remains disabled unless all safety gates are manually approved.")
    else:
        warning_box("Market regime currently blocks or limits BUY recommendations.")
    key_value_table(market)

