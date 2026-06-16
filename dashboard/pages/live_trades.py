from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.models import LiveTradeExecutionLog, PortfolioPosition, PortfolioTrade
from dashboard.ui_components import empty_state, professional_table, section_title


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])


def render() -> None:
    st.title("Live Trades")
    st.caption("Execution audit log, broker adapter status, and paper/live trade history. " + RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        execution_logs = db.scalars(select(LiveTradeExecutionLog).order_by(LiveTradeExecutionLog.created_at.desc()).limit(500)).all()
        trades = db.scalars(select(PortfolioTrade).order_by(PortfolioTrade.trade_date.desc()).limit(500)).all()
        positions = db.scalars(select(PortfolioPosition).order_by(PortfolioPosition.updated_at.desc()).limit(500)).all()

    c1, c2, c3 = st.columns(3)
    c1.metric("Execution Logs", len(execution_logs))
    c2.metric("Portfolio Trades", len(trades))
    c3.metric("Positions", len(positions))

    tabs = st.tabs(["Execution Log", "Portfolio Trades", "Open / Closed Positions"])
    with tabs[0]:
        section_title("Broker/Execution Audit")
        df = _df(execution_logs)
        professional_table(df, height=420) if not df.empty else empty_state("No execution log rows yet.")
    with tabs[1]:
        section_title("Portfolio Trade History")
        df = _df(trades)
        professional_table(df, height=420) if not df.empty else empty_state("No portfolio trades yet.")
    with tabs[2]:
        section_title("Positions")
        df = _df(positions)
        professional_table(df, height=420) if not df.empty else empty_state("No positions yet.")
