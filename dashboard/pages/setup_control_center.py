from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AutomationRun, PortfolioSetting, TelegramMessage, TelegramSource
from app.services.ai_llm_service import latest_opinions, run_ai_analysis
from app.services.automation_runner import get_automation_status, run_automation_cycle, set_automation_enabled
from app.services.dynamic_settings import list_settings, set_setting
from app.services.env_health import parse_env
from app.services.ingestion import run_ingestion_cycle
from app.services.system_health_check import run_health_check
from app.services.trading_safety import safety_snapshot
from app.services.tradingview_screener import run_tradingview_screening
from app.services.strategy_registry import run_all_enabled_strategies
from dashboard.ui_components import (
    empty_state,
    key_value_table,
    metric_card,
    professional_table,
    section_title,
    signal_badge,
    success_box,
    warning_box,
)

logger = logging.getLogger(__name__)


def _group_settings(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("key", ""))
        parts = key.split("_")
        group = parts[0] if parts else "general"
        if group not in ("automation", "combined", "final", "portfolio", "risk", "backtest", "alert", "ui", "daily", "live", "audit", "emergency", "max", "min", "data", "news", "ohlcv", "telegram", "market"):
            group = parts[0] if len(parts) > 0 else "general"
        special_map = {
            "automation": "Automation",
            "combined": "Weights",
            "final": "Weights",
            "portfolio": "Portfolio",
            "risk": "Risk Guards",
            "backtest": "Backtest",
            "alert": "Alerts",
            "ui": "UI",
            "daily": "Reports",
            "live": "Trading",
            "audit": "Trading",
            "emergency": "Trading",
            "max": "Risk Guards",
            "min": "Risk Guards",
            "data": "Data",
            "news": "Data",
            "ohlcv": "Data",
            "telegram": "Telegram",
            "market": "Data",
        }
        group_name = special_map.get(group, "General")
        groups.setdefault(group_name, []).append(row)
    return groups


def _editable_settings_page() -> None:
    section_title("All Settings")
    with SessionLocal() as db:
        all_settings = list_settings(db)
    groups = _group_settings(all_settings)
    tab_names = list(groups.keys())
    if tab_names:
        tabs = st.tabs(tab_names)
        for tab, group_name in zip(tabs, tab_names):
            with tab:
                rows = groups[group_name]
                df = pd.DataFrame(rows)
                if not df.empty:
                    edited = st.data_editor(
                        df[["key", "value", "value_type", "description"]],
                        disabled=["key", "value_type", "description"],
                        use_container_width=True,
                        hide_index=True,
                        key=f"settings_editor_{group_name}",
                    )
                    if st.button(f"Save {group_name} settings", key=f"save_{group_name}"):
                        with SessionLocal() as db:
                            for _, row in edited.iterrows():
                                set_setting(db, str(row["key"]), row["value"], value_type=str(row["value_type"]))
                        success_box(f"{group_name} settings saved.")


def render() -> None:
    st.title("Setup / Control Center")
    st.caption("Centralized dashboard for system configuration, automation control, and health monitoring.")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Automation Control", "System Health", "Trading Safety", "Settings", "Environment Config", "AI Analysis"
    ])

    with tab1:
        section_title("Automation Runner")
        status = get_automation_status()
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            enabled = status.get("enabled", False)
            metric_card("Automation", "Enabled" if enabled else "Disabled", "🟢" if enabled else "🔴")
            if st.button("Toggle Enable/Disable"):
                set_automation_enabled(not enabled)
                st.rerun()
        with c2:
            metric_card("Status", status.get("last_status", "unknown").replace("_", " ").title())
        with c3:
            metric_card("Interval", f'{status.get("interval_seconds", 120)}s')
        with c4:
            metric_card("Last Run", status.get("last_run_time", "Never") or "Never")

        if status.get("last_error"):
            warning_box(f"Last error: {status['last_error']}")

        if status.get("last_finished_at"):
            st.caption(f"Finished at: {status['last_finished_at']} | Next run: {status.get('next_run_time', 'N/A')}")

        section_title("Manual Actions")
        ac1, ac2, ac3, ac4 = st.columns(4)
        with ac1:
            if st.button("Run Automation Cycle Now"):
                with st.spinner("Running full automation cycle..."):
                    result = run_automation_cycle()
                    if result:
                        success_box(f"Automation cycle completed: {result.get('status', 'ok')}")
                    else:
                        warning_box("Automation cycle returned no result.")
        with ac2:
            if st.button("Fetch Telegram Only"):
                with st.spinner("Fetching Telegram messages..."):
                    result = run_ingestion_cycle()
                    success_box(f"Telegram fetch: {result}")
        with ac3:
            if st.button("Run TradingView Screening"):
                with st.spinner("Running TradingView screening..."):
                    settings = get_settings()
                    with SessionLocal() as db:
                        result = run_tradingview_screening(db, settings=settings)
                    success_box(f"Screening done: {len(result.get('rows', []))} rows")
        with ac4:
            if st.button("Run All Strategies"):
                with st.spinner("Running strategies..."):
                    with SessionLocal() as db:
                        result = run_all_enabled_strategies(db)
                    success_box(f"Strategies completed: {result}")

        section_title("Last Automation Run Detail")
        with SessionLocal() as db:
            latest = db.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
        if latest:
            detail = {
                "Run ID": latest.run_id,
                "Started": latest.started_at.strftime("%Y-%m-%d %H:%M:%S") if latest.started_at else "-",
                "Finished": latest.finished_at.strftime("%Y-%m-%d %H:%M:%S") if latest.finished_at else "-",
                "Duration": f"{latest.duration_seconds:.1f}s" if latest.duration_seconds else "-",
                "Status": latest.status,
                "Symbols": str(latest.symbols_processed),
                "Opportunities": str(latest.opportunities_count),
                "Alerts": str(latest.alerts_sent),
                "Telegram": latest.telegram_fetch_status or "-",
                "Strategy": latest.strategy_status or "-",
                "Backtest": latest.backtest_status or "-",
            }
            key_value_table(detail)
        else:
            empty_state("No automation runs yet.")

    with tab2:
        section_title("System Health Check")
        if st.button("Run Health Check Now"):
            with st.spinner("Checking system health..."):
                rows = run_health_check(save_log=False)
            if rows:
                df = pd.DataFrame(rows)
                display = df[["component", "status", "message"]].copy()
                professional_table(display)
                for row in rows:
                    if row.get("status") == "ERROR":
                        warning_box(f'{row["component"]}: {row["message"]}')
                    elif row.get("status") == "OK":
                        pass
                    else:
                        st.info(f'{row["component"]}: {row["message"]}')
        else:
            empty_state("Click 'Run Health Check Now' to assess system health.")

        section_title("Source & Ingestion Summary")
        with SessionLocal() as db:
            from sqlalchemy import func
            rows = db.execute(
                select(
                    TelegramSource.id,
                    TelegramSource.username,
                    TelegramSource.title,
                    TelegramSource.is_active,
                    TelegramSource.trust_score,
                    func.count(TelegramMessage.id).label("msg_count"),
                )
                .outerjoin(TelegramMessage, TelegramMessage.source_id == TelegramSource.id)
                .group_by(TelegramSource.id, TelegramSource.username, TelegramSource.title, TelegramSource.is_active, TelegramSource.trust_score)
                .order_by(TelegramSource.username)
            ).all()
        if rows:
            src_df = pd.DataFrame([
                {"username": r.username, "title": r.title, "active": r.is_active, "trust": r.trust_score, "messages": r.msg_count}
                for r in rows
            ])
            professional_table(src_df)
        else:
            empty_state("No Telegram sources configured.")

    with tab3:
        section_title("Trading Safety Status")
        with SessionLocal() as db:
            safety = safety_snapshot(db)
        safety_flags = {
            "Audit Mode": safety.get("audit_mode", False),
            "Emergency Stop": safety.get("emergency_stop_trading", False),
            "Live Trading": safety.get("live_trading_enabled", False),
            "Portfolio Bot": safety.get("portfolio_bot_enabled", False),
            "Auto Paper Trades": safety.get("portfolio_auto_execute_paper_trades", False),
            "Execution Blocked": safety.get("execution_blocked", False),
        }
        cols = st.columns(3)
        for i, (label, value) in enumerate(safety_flags.items()):
            with cols[i % 3]:
                st.markdown(
                    f'<div class="egx-chip" style="text-align:center;">{"🔴" if value else "🟢"} {label}: {"ON" if value else "OFF"}</div>',
                    unsafe_allow_html=True,
                )
        if safety.get("blocked_reasons"):
            for reason in safety["blocked_reasons"]:
                warning_box(f"Blocked: {reason}")
        if safety.get("daily_loss_pct") is not None:
            st.metric("Daily Loss %", f'{safety["daily_loss_pct"]:.2f}%')

        section_title("Portfolio Settings")
        with SessionLocal() as db:
            ps = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc()))
        if ps:
            ps_df = pd.DataFrame([
                {"Setting": "Initial Cash", "Value": f"{ps.initial_cash:,.0f}"},
                {"Setting": "Current Cash", "Value": f"{ps.current_cash:,.0f}"},
                {"Setting": "Max Risk/Trade", "Value": f"{ps.max_risk_per_trade_pct:.1f}%"},
                {"Setting": "Max Position Size", "Value": f"{ps.max_position_size_pct:.1f}%"},
                {"Setting": "Max Open Positions", "Value": str(ps.max_open_positions)},
                {"Setting": "Trading Mode", "Value": ps.trading_mode},
                {"Setting": "Manual Buy Confirm", "Value": str(ps.require_manual_buy_confirmation)},
                {"Setting": "Manual Sell Confirm", "Value": str(ps.require_manual_sell_confirmation)},
                {"Setting": "Max Daily Trades", "Value": str(ps.max_daily_trades)},
                {"Setting": "Bot Enabled", "Value": str(ps.portfolio_bot_enabled)},
            ])
            professional_table(ps_df)

    with tab4:
        _editable_settings_page()

    with tab5:
        section_title("Environment Configuration")
        env = parse_env()
        if env.get("exists"):
            st.success(f".env file found with {len(env.get('rows', []))} entries.")
            if env.get("duplicates"):
                warning_box(f"Duplicate keys found: {', '.join(env['duplicates'].keys())}")
            df = pd.DataFrame(env.get("rows", []))
            if not df.empty:
                display_df = df[["line", "key", "is_secret", "is_set"]].copy()
                display_df["key"] = display_df["key"].apply(
                    lambda k: k[:4] + "****" if df.loc[df["key"] == k, "is_secret"].any() else k
                )
                professional_table(display_df, height=400)
        else:
            warning_box("No .env file found at project root.")

    with tab6:
        section_title("AI Analysis Control")
        settings = get_settings()
        ai_enabled = settings.enable_ai_analysis
        st.markdown(
            f'<div class="egx-chip">AI Analysis: {"🟢 Enabled" if ai_enabled else "🔴 Disabled"} | Model: {settings.ai_model} | Max stocks/run: {settings.ai_max_stocks_per_run} | Min score: {settings.ai_min_score_to_analyze}</div>',
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Run AI Analysis Now", type="primary", use_container_width=True):
                with st.spinner("Generating AI opinions via OpenAI..."):
                    result = run_ai_analysis()
                if result.get("status") == "completed":
                    success_box(f"Analysis done: {result.get('symbols_analyzed', 0)} symbols, {result.get('errors', 0)} errors, {result.get('total_tokens_used', 0)} tokens")
                    st.json(result)
                else:
                    warning_box(f"Analysis failed or disabled: {result.get('reason', 'unknown')}")
        with c2:
            if st.button("Refresh Latest Opinions", use_container_width=True):
                st.rerun()

        section_title("Latest AI Opinions")
        opinions = latest_opinions(limit=50)
        if opinions:
            df = pd.DataFrame(opinions)
            cols = ["symbol", "ai_score", "ai_signal", "ai_confidence", "ai_time_horizon", "tokens_used", "latency_ms", "created_at"]
            display = df[[c for c in cols if c in df.columns]].copy()
            if not display.empty:
                display.columns = [c.replace("_", " ").title() for c in display.columns]
                professional_table(display)
            st.caption(f"Total opinions stored: {len(opinions)}")
        else:
            empty_state("No AI opinions generated yet. Click 'Run AI Analysis Now' above.")
