from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.backtesting.backtest_engine import backtest_all_strategies
from app.config import RESEARCH_DISCLAIMER
from app.data.egx_symbols import list_active_symbols
from app.database import SessionLocal
from app.models import StrategyPerformance


def render() -> None:
    st.title("Backtesting Page")
    st.caption(RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        symbols = list_active_symbols(db)
    symbol = st.selectbox("Symbol", symbols or ["COMI"], key="modular_backtest_symbol")
    if st.button("Run modular strategy backtests"):
        with SessionLocal() as db, st.spinner(f"Backtesting modular strategies for {symbol}..."):
            result = backtest_all_strategies(db, symbol, persist=True)
            db.commit()
        st.dataframe(pd.DataFrame(result), use_container_width=True, hide_index=True)
    with SessionLocal() as db:
        rows = db.scalars(select(StrategyPerformance).order_by(StrategyPerformance.updated_at.desc()).limit(500)).all()
    df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])
    st.dataframe(df, use_container_width=True, hide_index=True) if not df.empty else st.info("No modular backtest performance rows yet.")

