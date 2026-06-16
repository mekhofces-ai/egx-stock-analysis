from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.portfolio_bot import get_portfolio_settings, portfolio_value
from app.models import LiveTradeExecutionLog, PortfolioTrade
from app.services.dynamic_settings import get_bool, get_float, get_int, seed_dynamic_settings, set_setting
from app.services.market_daily_evaluation import evaluate_daily_market
from app.services.telegram_bot import send_private_message_sync
from app.services.trading_safety import disable_trading_for_audit, safety_snapshot
from dashboard.ui_components import key_value_table, professional_table, success_box, warning_box


def _set_bool(key: str, value: bool) -> None:
    with SessionLocal() as db:
        set_setting(db, key, "true" if value else "false", value_type="bool")
        db.commit()


def render() -> None:
    st.title("Trading Control Center")
    st.caption("Controlled trading settings. Live broker execution remains blocked unless every safety rule passes. " + RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        safety = safety_snapshot(db)
        settings = get_portfolio_settings(db)
        values = portfolio_value(db, settings)
        market = evaluate_daily_market(db, persist=True)
        total_trades = db.scalar(select(func.count()).select_from(PortfolioTrade)) or 0
        last_order = db.scalar(select(LiveTradeExecutionLog).order_by(LiveTradeExecutionLog.created_at.desc()))
        config = {
            "max_daily_trades": get_int(db, "max_daily_trades", 5),
            "max_daily_buy_trades": get_int(db, "max_daily_buy_trades", 2),
            "max_position_size_percent": get_float(db, "max_position_size_percent", 20.0),
            "max_total_portfolio_exposure_percent": get_float(db, "max_total_portfolio_exposure_percent", 80.0),
            "min_confidence_to_trade": get_float(db, "min_confidence_to_trade", 75.0),
            "first_live_trade_approved": get_bool(db, "first_live_trade_approved", False),
            "portfolio_auto_execution_enabled": get_bool(db, "portfolio_auto_execution_enabled", False),
        }
        db.commit()

    mode = "Audit" if safety.get("audit_mode") else "Live" if safety.get("live_trading_enabled") else "Paper"
    cols = st.columns(5)
    cols[0].metric("Mode", mode)
    cols[1].metric("Live Trading", "ON" if safety.get("live_trading_enabled") else "OFF")
    cols[2].metric("Emergency Stop", "ON" if safety.get("emergency_stop_enabled") else "OFF")
    cols[3].metric("Auto Execution", "ON" if config["portfolio_auto_execution_enabled"] else "OFF")
    cols[4].metric("Market Permission", market.get("trade_permission"))
    if safety.get("live_trading_enabled"):
        st.error("LIVE TRADING CAPABILITY IS ENABLED. Orders are still blocked unless emergency stop is off, audit mode is off, auto execution is on, market permits trading, and first live trade is approved.")
    if safety.get("execution_blocked"):
        warning_box("Execution is currently blocked: " + ", ".join(safety.get("blocked_reasons") or []))
    else:
        success_box("No global safety block is active. Per-trade validation still applies.")

    st.subheader("Portfolio And Limits")
    c = st.columns(5)
    c[0].metric("Portfolio Value", f"{values['total_value']:,.0f} EGP")
    c[1].metric("Cash", f"{values['cash']:,.0f} EGP")
    c[2].metric("Exposure", f"{values['invested']:,.0f} EGP")
    c[3].metric("Today P/L", f"{values['profit_loss']:,.0f} EGP", f"{values['profit_loss_pct']:.2f}%")
    c[4].metric("All Trades Logged", int(total_trades))
    key_value_table(config)

    st.subheader("Mode Controls")
    a, b, c, d = st.columns(4)
    if a.button("Enable Paper Mode"):
        with SessionLocal() as db:
            set_setting(db, "paper_trading_enabled", "true", "bool")
            set_setting(db, "audit_mode", "false", "bool")
            set_setting(db, "audit_mode_enabled", "false", "bool")
            set_setting(db, "live_trading_enabled", "false", "bool")
            set_setting(db, "portfolio_auto_execution_enabled", "false", "bool")
            db.commit()
        success_box("Paper mode enabled. Live trading remains disabled.")
    confirm_live = st.checkbox("I understand live mode can place real orders only after all safety checks pass.", key="confirm_live_mode")
    if b.button("Enable Live Capability", disabled=not confirm_live):
        with SessionLocal() as db:
            set_setting(db, "live_trading_enabled", "true", "bool")
            set_setting(db, "paper_trading_enabled", "false", "bool")
            set_setting(db, "audit_mode", "false", "bool")
            set_setting(db, "audit_mode_enabled", "false", "bool")
            set_setting(db, "portfolio_auto_execution_enabled", "false", "bool")
            set_setting(db, "first_live_trade_approved", "false", "bool")
            db.commit()
        warning_box("Live capability enabled, but auto execution is OFF and first live trade is not approved.")
    if c.button("Disable Live Mode"):
        with SessionLocal() as db:
            disable_trading_for_audit(db)
            db.commit()
        warning_box("Live mode disabled. Audit/emergency safety is active.")
    if d.button("Emergency Stop", type="primary"):
        with SessionLocal() as db:
            set_setting(db, "emergency_stop_trading", "true", "bool")
            set_setting(db, "emergency_stop_enabled", "true", "bool")
            set_setting(db, "portfolio_auto_execution_enabled", "false", "bool")
            db.commit()
        warning_box("Emergency stop enabled immediately.")

    st.subheader("Advanced Live Controls")
    e, f, g = st.columns(3)
    if e.button("Reset Emergency Stop"):
        _set_bool("emergency_stop_trading", False)
        _set_bool("emergency_stop_enabled", False)
        warning_box("Emergency stop reset. Confirm all other safety settings before any trade.")
    if f.button("Approve First Live Trade"):
        _set_bool("first_live_trade_approved", True)
        warning_box("First-live-trade approval flag set. Per-trade validation and broker adapter still apply.")
    if g.button("Enable Auto Execution"):
        _set_bool("portfolio_auto_execution_enabled", True)
        warning_box("Auto execution flag enabled. Live orders still require all validation checks and broker adapter.")

    st.subheader("Manual Safe Actions")
    x, y, z = st.columns(3)
    if x.button("Run Market Evaluation Now"):
        with SessionLocal() as db:
            result = evaluate_daily_market(db, persist=True)
            db.commit()
        key_value_table(result)
    if y.button("Send Test Telegram Alert"):
        try:
            send_private_message_sync("EGX trading control test. Message mode: WATCH ONLY / audit-safe check.")
            success_box("Telegram test sent.")
        except Exception as exc:
            warning_box(f"Telegram test failed: {exc}")
    if z.button("Disable Everything Safely"):
        with SessionLocal() as db:
            disable_trading_for_audit(db)
            db.commit()
        warning_box("Audit mode and emergency stop are active.")

    st.subheader("Last Order")
    if last_order:
        key_value_table({k: v for k, v in last_order.__dict__.items() if not k.startswith("_")})
    else:
        st.info("No live execution log rows yet.")

    with SessionLocal() as db:
        rows = db.scalars(select(LiveTradeExecutionLog).order_by(LiveTradeExecutionLog.created_at.desc()).limit(100)).all()
    df = pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])
    professional_table(df, height=360)
