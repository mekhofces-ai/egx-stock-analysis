from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.financial.financial_data import FIELD_ALIASES, import_financial_csv
from app.financial.financial_engine import analyze_financial
from app.models import FinancialData, FinancialSignal, Stock
from dashboard.ui_components import data_gap_box, key_value_table, professional_table, section_title, success_box


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([row.__dict__ for row in rows]).drop(columns=["_sa_instance_state"], errors="ignore")


def _latest_signal_rows(db, limit: int = 500) -> list[FinancialSignal]:
    return db.scalars(
        select(FinancialSignal)
        .order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc())
        .limit(limit)
    ).all()


def render() -> None:
    st.title("Financial Analysis")
    st.caption(RESEARCH_DISCLAIMER)
    st.info(
        "This page shows real uploaded financial statement rows and the financial signals calculated from them. "
        "If raw financial rows are zero, the engine keeps the final decision running with a neutral placeholder score instead of fake ratios."
    )

    with SessionLocal() as db:
        raw_count = db.scalar(select(func.count()).select_from(FinancialData)) or 0
        signal_count = db.scalar(select(func.count()).select_from(FinancialSignal)) or 0
        symbol_count = db.scalar(select(func.count(func.distinct(FinancialData.symbol)))) or 0
        latest_signal = db.scalar(select(FinancialSignal).order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc()))
        symbols = [row.symbol for row in db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all()]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw financial rows", raw_count)
    c2.metric("Symbols with statements", symbol_count)
    c3.metric("Financial signals", signal_count)
    c4.metric("Latest signal", str(latest_signal.signal_date)[:16] if latest_signal else "-")

    if raw_count == 0:
        data_gap_box(
            "No raw financial statement data is imported yet",
            "Financial Analysis exists, but it cannot calculate real revenue, profit, valuation, debt, ROE, ROA, or cash-flow ratios until you upload real financial data.",
            "Upload a CSV below or from Admin Settings. Required column: symbol. Useful columns: period, revenue, gross_profit, net_profit, eps, assets, liabilities, equity, debt, cash_flow, market_price, shares_outstanding.",
        )

    with st.expander("Upload financial CSV", expanded=raw_count == 0):
        st.write("Accepted column aliases:")
        alias_rows = [{"field": field, "accepted_names": ", ".join(names)} for field, names in FIELD_ALIASES.items()]
        professional_table(pd.DataFrame(alias_rows), height=260)
        financial_file = st.file_uploader("Financial CSV", type=["csv"], key="financial_analysis_csv")
        if financial_file and st.button("Import financial CSV", type="primary"):
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
                    handle.write(financial_file.getvalue())
                    temp_path = handle.name
                with SessionLocal() as db:
                    imported = import_financial_csv(db, temp_path)
                    db.commit()
                success_box(f"Imported {imported} financial row(s). Refresh or run analysis for selected symbols.")
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    selected_symbol = st.selectbox("Symbol", symbols or ["COMI"], key="financial_symbol")
    if st.button("Run financial analysis for selected symbol"):
        with SessionLocal() as db:
            result = analyze_financial(db, selected_symbol, persist=True)
            db.commit()
        success_box(f"Financial analysis refreshed for {selected_symbol}: {result.get('financial_signal')} at {float(result.get('financial_score') or 0):.0f}%.")

    tabs = st.tabs(["Latest Signals", "Selected Symbol", "Raw Data"])
    with SessionLocal() as db:
        signals_df = _df(_latest_signal_rows(db))
        selected_signal = db.scalar(
            select(FinancialSignal)
            .where(FinancialSignal.symbol == selected_symbol)
            .order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc())
        )
        selected_raw = _df(
            db.scalars(
                select(FinancialData)
                .where(FinancialData.symbol == selected_symbol)
                .order_by(FinancialData.period.desc(), FinancialData.created_at.desc())
                .limit(20)
            ).all()
        )
        raw_df = _df(
            db.scalars(
                select(FinancialData)
                .order_by(FinancialData.created_at.desc(), FinancialData.id.desc())
                .limit(300)
            ).all()
        )

    with tabs[0]:
        visible = [
            "symbol",
            "financial_signal",
            "financial_score",
            "profitability_score",
            "growth_score",
            "valuation_score",
            "debt_score",
            "cashflow_score",
            "risk_level",
            "reason",
            "signal_date",
        ]
        professional_table(signals_df[[col for col in visible if col in signals_df.columns]], height=520)

    with tabs[1]:
        section_title(f"{selected_symbol} Financial Signal")
        if selected_signal:
            key_value_table(
                {
                    "signal": selected_signal.financial_signal,
                    "score": f"{float(selected_signal.financial_score or 0):.0f}%",
                    "profitability": selected_signal.profitability_score,
                    "growth": selected_signal.growth_score,
                    "valuation": selected_signal.valuation_score,
                    "debt": selected_signal.debt_score,
                    "cashflow": selected_signal.cashflow_score,
                    "risk": selected_signal.risk_level,
                    "reason": selected_signal.reason,
                    "last_update": selected_signal.signal_date,
                }
            )
        else:
            data_gap_box("No financial signal for this symbol", "Run financial analysis after importing data, or let the final decision engine create a neutral fallback.")
        section_title("Raw Statement Rows")
        if selected_raw.empty:
            data_gap_box("No raw rows for selected symbol", "This symbol has no uploaded financial statements yet.")
        else:
            professional_table(selected_raw.drop(columns=["raw_json"], errors="ignore"), height=300)

    with tabs[2]:
        if raw_df.empty:
            data_gap_box("Raw financial table is empty", "Upload a CSV to populate financial_data. No fake financial rows are created.")
        else:
            professional_table(raw_df.drop(columns=["raw_json"], errors="ignore"), height=520)
