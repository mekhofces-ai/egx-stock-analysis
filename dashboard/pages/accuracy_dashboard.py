from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.accuracy_tracker import update_signal_accuracy
from app.intelligence.learning_engine import update_dynamic_weights
from app.intelligence.review_engine import best_analysis_by_market_condition, best_analysis_by_symbol, confidence_calibration
from app.models import DynamicWeightsBySymbol, SignalAccuracyTracking
from dashboard.ui_components import empty_state, professional_table, section_title


def _rate(series: pd.Series) -> float:
    clean = series.dropna()
    if clean.empty:
        return 0.0
    return round(clean.astype(bool).mean() * 100, 2)


def render() -> None:
    st.title("Accuracy Center")
    st.caption(RESEARCH_DISCLAIMER)
    c1, c2, c3 = st.columns(3)
    if c1.button("Update signal accuracy"):
        with SessionLocal() as db:
            result = update_signal_accuracy(db)
            db.commit()
        st.success(f"Updated {result['updated']} accuracy rows. Missing prices: {result['missing_prices']}.")
    if c2.button("Recalculate learning weights"):
        with SessionLocal() as db:
            result = update_dynamic_weights(db)
            db.commit()
        st.success(f"Updated {result['updated']} symbol weights. Skipped: {result['skipped']}.")
    if c3.button("Rebuild confidence calibration"):
        with SessionLocal() as db:
            rows = confidence_calibration(db, persist=True)
            db.commit()
        st.success(f"Calibration buckets rebuilt: {len(rows)}.")
    with SessionLocal() as db:
        rows = db.scalars(select(SignalAccuracyTracking).order_by(SignalAccuracyTracking.updated_at.desc()).limit(1000)).all()
        weights = db.scalars(select(DynamicWeightsBySymbol).order_by(DynamicWeightsBySymbol.updated_at.desc()).limit(300)).all()
        calibration_rows = pd.DataFrame(confidence_calibration(db, persist=False))
        best_symbol_rows = pd.DataFrame(best_analysis_by_symbol(db))
        best_regime_rows = pd.DataFrame(best_analysis_by_market_condition(db))
    df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])
    if df.empty:
        empty_state("No accuracy rows yet. This needs final decisions plus later OHLCV prices.")
    else:
        metrics = st.columns(6)
        for idx, col in enumerate(["technical_correct", "financial_correct", "news_correct", "telegram_correct", "strategy_correct", "final_decision_correct"]):
            metrics[idx].metric(col.replace("_correct", "").title(), f"{_rate(df[col]):.0f}%")
        section_title("Accuracy Detail")
        professional_table(df, height=360)
    wdf = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in weights])
    tabs = st.tabs(["Dynamic Weights", "Best Per Stock", "Best Per Market Condition", "Confidence Calibration"])
    with tabs[0]:
        professional_table(wdf, height=320) if not wdf.empty else empty_state("No dynamic weights calculated yet.")
    with tabs[1]:
        professional_table(best_symbol_rows, height=360) if not best_symbol_rows.empty else empty_state("No best-analysis rows yet.")
    with tabs[2]:
        professional_table(best_regime_rows, height=320) if not best_regime_rows.empty else empty_state("No market-regime accuracy rows yet.")
    with tabs[3]:
        if calibration_rows.empty:
            empty_state("No calibration rows yet. Calibration needs historical correctness results.")
        else:
            chart_df = calibration_rows[["bucket", "expected_confidence", "observed_win_rate"]].set_index("bucket")
            st.line_chart(chart_df, use_container_width=True)
            professional_table(calibration_rows)
