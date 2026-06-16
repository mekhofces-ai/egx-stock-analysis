from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.intelligence.final_decision_engine import latest_final_decision
from app.intelligence.portfolio_bot import execute_paper_sell, get_portfolio_settings, portfolio_value, run_daily_portfolio_bot, scan_portfolio
from app.intelligence.risk_guard import risk_guard_status
from app.intelligence.trade_approval import set_trade_approval_status
from app.models import PortfolioPosition, PortfolioSetting, PortfolioTrade, TradeApproval
from app.services.dynamic_settings import get_bool, get_int, seed_dynamic_settings, set_setting
from app.services.trading_safety import execution_block_reason, safety_snapshot
from dashboard.ui_components import empty_state, key_value_table, professional_table, risk_badge, section_title, success_box, warning_box


def _df(rows: list[object]) -> pd.DataFrame:
    return pd.DataFrame([{k: v for k, v in row.__dict__.items() if not k.startswith("_")} for row in rows])


def render() -> None:
    st.title("Portfolio Bot")
    st.caption("Paper trading / virtual portfolio only. " + RESEARCH_DISCLAIMER)
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        settings = get_portfolio_settings(db)
        values = portfolio_value(db, settings)
        guard = risk_guard_status(db, settings)
        safety = safety_snapshot(db)
        automation_scan_enabled = get_bool(db, "automation_run_portfolio_bot", False)
        auto_execute_enabled = get_bool(db, "portfolio_bot_auto_execute_paper_trades", False)
        portfolio_symbol_limit = get_int(db, "portfolio_bot_symbol_limit", 50, minimum=1)
        open_positions = db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open").order_by(PortfolioPosition.buy_date.desc())).all()
        trades = db.scalars(select(PortfolioTrade).order_by(PortfolioTrade.trade_date.desc()).limit(500)).all()
        approvals = db.scalars(select(TradeApproval).order_by(TradeApproval.updated_at.desc()).limit(200)).all()
        db.commit()
    c = st.columns(5)
    c[0].metric("Initial cash", f"{settings.initial_cash:,.0f} EGP")
    c[1].metric("Current cash", f"{values['cash']:,.0f} EGP")
    c[2].metric("Invested", f"{values['invested']:,.0f} EGP")
    c[3].metric("Total value", f"{values['total_value']:,.0f} EGP")
    c[4].metric("P/L", f"{values['profit_loss']:,.0f} EGP", f"{values['profit_loss_pct']:.2f}%")
    guard_text = "Risk guard clear." if guard.get("allowed") else "; ".join(guard.get("reasons") or ["Risk guard blocked trading."])
    (success_box if guard.get("allowed") else warning_box)(guard_text)
    if safety.get("execution_blocked"):
        warning_box(
            "Audit/emergency safety is active. Portfolio automation and paper execution are blocked. "
            f"Reasons: {', '.join(safety.get('blocked_reasons') or [])}"
        )
    status_cols = st.columns(4)
    status_cols[0].metric("Portfolio bot", "On" if settings.portfolio_bot_enabled else "Off")
    status_cols[1].metric("Automation scan", "On" if automation_scan_enabled else "Off")
    status_cols[2].metric("Auto paper execution", "On" if auto_execute_enabled else "Off")
    status_cols[3].metric("Scan limit", portfolio_symbol_limit)

    s1, s2, s3 = st.columns(3)
    if s1.button("Start auto paper trading"):
        with SessionLocal() as db:
            row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
            row.portfolio_bot_enabled = True
            row.trading_mode = "paper_trading"
            row.timezone = "Africa/Cairo"
            row.require_manual_buy_confirmation = False
            row.require_manual_sell_confirmation = False
            db.add(row)
            set_setting(db, "automation_run_portfolio_bot", "true", value_type="bool")
            set_setting(db, "portfolio_bot_auto_execute_paper_trades", "true", value_type="bool")
            db.commit()
        st.success("Auto paper trading is enabled. Automation can now execute virtual BUY/SELL trades.")
    if s2.button("Start approval mode"):
        with SessionLocal() as db:
            row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
            row.portfolio_bot_enabled = True
            row.trading_mode = "paper_trading"
            row.timezone = "Africa/Cairo"
            row.require_manual_buy_confirmation = True
            row.require_manual_sell_confirmation = True
            db.add(row)
            set_setting(db, "automation_run_portfolio_bot", "true", value_type="bool")
            set_setting(db, "portfolio_bot_auto_execute_paper_trades", "false", value_type="bool")
            db.commit()
        st.success("Portfolio bot is enabled in approval mode. It will propose trades without auto-execution.")
    if s3.button("Stop portfolio automation"):
        with SessionLocal() as db:
            row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
            row.portfolio_bot_enabled = False
            db.add(row)
            set_setting(db, "automation_run_portfolio_bot", "false", value_type="bool")
            set_setting(db, "portfolio_bot_auto_execute_paper_trades", "false", value_type="bool")
            db.commit()
        st.success("Portfolio automation stopped.")

    a1, a2, a3, a4 = st.columns(4)
    if a1.button("Run portfolio scan now"):
        with SessionLocal() as db, st.spinner("Scanning portfolio candidates..."):
            result = scan_portfolio(db, execute=False, limit=50)
            db.commit()
        st.dataframe(pd.DataFrame(result["actions"]), use_container_width=True, hide_index=True)
    if a2.button("Execute paper trades"):
        with SessionLocal() as db, st.spinner("Executing eligible paper trades..."):
            result = run_daily_portfolio_bot(db, execute=True, force=False, limit=50)
            db.commit()
        st.dataframe(pd.DataFrame(result["actions"]), use_container_width=True, hide_index=True)
    if a3.button("Reset paper portfolio"):
        with SessionLocal() as db:
            settings = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
            db.query(PortfolioPosition).delete()
            db.query(PortfolioTrade).delete()
            settings.current_cash = settings.initial_cash
            db.add(settings)
            db.commit()
        st.success("Paper portfolio reset.")
    if a4.button("Refresh valuation"):
        with SessionLocal() as db:
            portfolio_value(db)
            db.commit()
        st.success("Portfolio valuation refreshed.")

    tabs = st.tabs(["Overview", "Open Positions", "Closed Trades", "Pending Approvals", "Manual Close", "Risk Guard"])
    with tabs[0]:
        if trades:
            trade_df = _df(list(reversed(trades)))
            trade_df["cum_pnl"] = trade_df["profit_loss"].fillna(0).cumsum()
            st.line_chart(trade_df[["trade_date", "cum_pnl"]].set_index("trade_date"), use_container_width=True)
        else:
            empty_state("No portfolio trades yet. Run a scan to generate paper-trade candidates.")
    pos_df = _df(open_positions)
    with tabs[1]:
        professional_table(pos_df) if not pos_df.empty else empty_state("No open paper positions.")
    trade_df = _df(trades)
    with tabs[2]:
        professional_table(trade_df) if not trade_df.empty else empty_state("No paper trades yet.")
    if not trade_df.empty:
        tabs[2].download_button("Export trades CSV", trade_df.to_csv(index=False).encode("utf-8"), "egx_paper_trades.csv", "text/csv")
    with tabs[3]:
        approval_df = _df(approvals)
        pending = approval_df[approval_df["status"].eq("pending")] if not approval_df.empty and "status" in approval_df else pd.DataFrame()
        if pending.empty:
            empty_state("No pending trade approvals.")
        else:
            professional_table(pending[["id", "symbol", "side", "proposed_price", "quantity", "total_value", "final_score", "signal", "reason", "created_at"]])
            selected_approval = st.selectbox("Approval", [f"{int(row.id)} - {row.symbol} {row.side}" for row in approvals if row.status == "pending"])
            approval_id = int(selected_approval.split(" - ", 1)[0])
            ca, cr = st.columns(2)
            if ca.button("Approve selected paper trade"):
                with SessionLocal() as db:
                    approval = db.get(TradeApproval, approval_id)
                    action = {"status": "missing_approval"}
                    if approval and approval.side == "BUY":
                        decision = latest_final_decision(db, approval.symbol)
                        if decision:
                            from app.intelligence.portfolio_bot import execute_paper_buy

                            action = execute_paper_buy(db, decision, force=True)
                        else:
                            action = {"status": "skipped", "reason": "No latest final decision found for this symbol."}
                    set_trade_approval_status(db, approval_id, "approved", approved_by="dashboard")
                    db.commit()
                st.success(action)
            if cr.button("Reject selected paper trade"):
                with SessionLocal() as db:
                    set_trade_approval_status(db, approval_id, "rejected", approved_by="dashboard")
                    db.commit()
                st.success("Approval rejected.")
    with tabs[4]:
        if open_positions:
            selected = st.selectbox("Position", [f"{row.id} - {row.symbol}" for row in open_positions])
            if st.button("Close selected position manually"):
                position_id = int(selected.split(" - ", 1)[0])
                with SessionLocal() as db:
                    position = db.get(PortfolioPosition, position_id)
                    if position:
                        result = execute_paper_sell(db, position, reason="Manual dashboard close.", force=True)
                        db.commit()
                        st.success(result)
        else:
            st.info("No open positions to close.")
    with tabs[5]:
        section_title("Risk Guard Status")
        st.markdown(risk_badge("LOW" if guard.get("allowed") else "HIGH"), unsafe_allow_html=True)
        key_value_table(
            {
                "Allowed": "Yes" if guard.get("allowed") else "No",
                "Reasons": ", ".join(guard.get("reasons") or ["None"]),
                "Daily P/L %": guard.get("daily_loss_pct"),
                "Weekly P/L %": guard.get("weekly_loss_pct"),
                "Drawdown %": guard.get("drawdown_pct"),
                "Consecutive losses": guard.get("consecutive_losses"),
            }
        )
