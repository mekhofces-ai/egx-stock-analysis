from __future__ import annotations

import pandas as pd
import streamlit as st

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.services.trading_alerts import build_trading_alerts, format_trading_alerts_message, send_trading_alerts
from dashboard.ui_components import empty_state, professional_table, section_title, success_box, warning_box


DISPLAY_COLUMNS = [
    "alert_type",
    "symbol",
    "company_name",
    "action",
    "confidence",
    "final_score",
    "current_price",
    "entry_price",
    "stop_loss",
    "target_1",
    "target_2",
    "trigger_price",
    "risk_level",
    "source",
    "market_regime",
    "reason",
]


def _alerts_df(alerts: list[dict]) -> pd.DataFrame:
    if not alerts:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)
    df = pd.DataFrame(alerts)
    for column in DISPLAY_COLUMNS:
        if column not in df.columns:
            df[column] = None
    return df[DISPLAY_COLUMNS]


def _metric_row(df: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("All alerts", len(df))
    c2.metric("BUY", int(df["alert_type"].eq("BUY").sum()) if not df.empty else 0)
    c3.metric("SELL", int(df["alert_type"].eq("SELL").sum()) if not df.empty else 0)
    c4.metric("TAKE PROFIT", int(df["alert_type"].eq("TAKE PROFIT").sum()) if not df.empty else 0)


def _show_table(df: pd.DataFrame, label: str) -> None:
    section_title(label)
    if df.empty:
        empty_state(f"No {label.lower()} right now.")
        return
    professional_table(df, height=460)
    selected = st.selectbox(
        f"{label} detail",
        [f"{row.alert_type} | {row.symbol} | {row.final_score or '-'}" for row in df.itertuples()],
        key=f"trading_alerts_detail_{label}",
    )
    idx = [f"{row.alert_type} | {row.symbol} | {row.final_score or '-'}" for row in df.itertuples()].index(selected)
    row = df.iloc[idx]
    st.text_area("Reason", value=str(row.get("reason") or "-"), height=150, key=f"trading_alerts_reason_{label}")


def render() -> None:
    st.title("Trading Alerts")
    st.caption("Only BUY, SELL, and TAKE PROFIT alerts. " + RESEARCH_DISCLAIMER)
    st.info(
        "Alerts are generated from final weighted decisions, opportunities, the daily stock report, combined analysis, "
        "portfolio positions, current prices, stop losses, and take-profit levels."
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    min_buy_score = c1.slider("Minimum BUY score", 50, 95, 65, step=1)
    include_sell_without_position = c2.checkbox("Show SELL/AVOID even without open position", value=True)
    limit = c3.slider("Maximum alerts", 10, 300, 100, step=10)

    with SessionLocal() as db:
        alerts = build_trading_alerts(
            db,
            min_buy_score=float(min_buy_score),
            include_sell_without_position=include_sell_without_position,
            limit=int(limit),
        )
    df = _alerts_df(alerts)
    _metric_row(df)

    b1, b2 = st.columns([1, 3])
    if b1.button("Send visible alerts to Telegram"):
        try:
            result = send_trading_alerts(df.to_dict("records"))
            if result.get("sent"):
                success_box(
                    f"Sent trading alerts to Telegram. Items: {result.get('items', 0)}. "
                    f"Skipped duplicates/limits: {result.get('skipped_duplicate', 0)}."
                )
            else:
                warning_box(result.get("message") or "No alert was sent.")
        except Exception as exc:
            warning_box(f"Telegram send failed: {exc}")
    with b2.expander("Telegram preview", expanded=False):
        st.text_area("Message", value=format_trading_alerts_message(df.to_dict("records")), height=260)

    tabs = st.tabs(["All", "BUY", "SELL", "TAKE PROFIT"])
    with tabs[0]:
        _show_table(df, "All Alerts")
    with tabs[1]:
        _show_table(df[df["alert_type"].eq("BUY")] if not df.empty else df, "BUY Alerts")
    with tabs[2]:
        _show_table(df[df["alert_type"].eq("SELL")] if not df.empty else df, "SELL Alerts")
    with tabs[3]:
        _show_table(df[df["alert_type"].eq("TAKE PROFIT")] if not df.empty else df, "TAKE PROFIT Alerts")
