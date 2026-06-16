from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    AUDIT_MODE,
    EMERGENCY_STOP_TRADING,
    LIVE_TRADING_ENABLED,
    MAX_DAILY_LOSS_PCT,
    RISK_NOTE,
)
from app.models import AutomationSetting, PortfolioPosition, PortfolioSetting, PortfolioTrade, TradeJournal
from app.services.dynamic_settings import get_bool, get_float, seed_dynamic_settings, set_setting


logger = logging.getLogger(__name__)


EMERGENCY_STOP_MESSAGE = (
    "Emergency Stop Enabled\n\n"
    "Live trading is disabled.\n"
    "System is currently in audit/simulation mode only."
)


def _bool_setting(db: Session, key: str, default: bool) -> bool:
    return bool(get_bool(db, key, default))


def safety_snapshot(db: Session) -> dict[str, Any]:
    seed_dynamic_settings(db)
    settings = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc()))
    audit_mode = _bool_setting(db, "audit_mode", AUDIT_MODE) or _bool_setting(db, "audit_mode_enabled", AUDIT_MODE)
    emergency_stop = _bool_setting(db, "emergency_stop_trading", EMERGENCY_STOP_TRADING) or _bool_setting(db, "emergency_stop_enabled", EMERGENCY_STOP_TRADING)
    live_enabled = _bool_setting(db, "live_trading_enabled", LIVE_TRADING_ENABLED)
    paper_enabled = _bool_setting(db, "paper_trading_enabled", True)
    auto_execution = _bool_setting(db, "portfolio_auto_execution_enabled", False)
    auto_paper = _bool_setting(db, "portfolio_bot_auto_execute_paper_trades", False)
    portfolio_auto = _bool_setting(db, "automation_run_portfolio_bot", False)
    daily_loss_limit = get_float(db, "max_daily_loss_pct", MAX_DAILY_LOSS_PCT)
    daily_loss_limit_percent = get_float(db, "max_daily_loss_percent", daily_loss_limit * 100)
    daily_loss = daily_loss_pct(db, settings=settings)
    blocked_reasons: list[str] = []
    if audit_mode:
        blocked_reasons.append("audit mode enabled")
    if emergency_stop:
        blocked_reasons.append("emergency stop enabled")
    if not live_enabled:
        blocked_reasons.append("live trading disabled")
    if daily_loss >= daily_loss_limit_percent:
        blocked_reasons.append("daily loss limit reached")
    return {
        "audit_mode": audit_mode,
        "audit_mode_enabled": audit_mode,
        "emergency_stop_trading": emergency_stop,
        "emergency_stop_enabled": emergency_stop,
        "live_trading_enabled": live_enabled,
        "paper_trading_enabled": paper_enabled,
        "portfolio_auto_execution_enabled": auto_execution,
        "portfolio_bot_enabled": bool(settings.portfolio_bot_enabled) if settings else False,
        "portfolio_auto_scan": portfolio_auto,
        "portfolio_auto_execute_paper_trades": auto_paper,
        "daily_loss_pct": round(daily_loss, 2),
        "daily_loss_limit_pct": round(daily_loss_limit_percent, 2),
        "execution_blocked": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
        "risk_note": RISK_NOTE,
    }


def daily_loss_pct(db: Session, settings: PortfolioSetting | None = None) -> float:
    settings = settings or db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc()))
    if not settings or not settings.initial_cash:
        return 0.0
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    trades = db.scalars(select(PortfolioTrade).where(PortfolioTrade.trade_date >= start)).all()
    realized_losses = sum(float(row.profit_loss or 0.0) for row in trades if float(row.profit_loss or 0.0) < 0)
    open_positions = db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open")).all()
    unrealized_losses = sum(float(row.unrealized_profit or 0.0) for row in open_positions if float(row.unrealized_profit or 0.0) < 0)
    loss = abs(realized_losses + unrealized_losses)
    return (loss / float(settings.initial_cash)) * 100.0


def execution_block_reason(db: Session, *, block_paper_execution: bool = True) -> str | None:
    snapshot = safety_snapshot(db)
    reasons: list[str] = []
    if not block_paper_execution:
        if not snapshot.get("paper_trading_enabled", True):
            reasons.append("paper trading disabled")
        if snapshot["daily_loss_pct"] >= snapshot["daily_loss_limit_pct"]:
            reasons.append("daily loss limit reached")
        return "; ".join(reasons) if reasons else None

    if snapshot["emergency_stop_trading"]:
        reasons.append("emergency stop enabled")
    if not snapshot["live_trading_enabled"]:
        reasons.append("live trading disabled")
    if snapshot["daily_loss_pct"] >= snapshot["daily_loss_limit_pct"]:
        reasons.append("daily loss limit reached")
    if block_paper_execution:
        if snapshot["audit_mode"]:
            reasons.append("audit mode enabled")
        if snapshot["audit_mode"] or snapshot["emergency_stop_trading"]:
            reasons.append("paper execution blocked during audit/emergency mode")
    if reasons:
        return "; ".join(reasons)
    return None


def guard_trade_execution(db: Session, *, execution_type: str = "paper") -> dict[str, Any] | None:
    block_paper = execution_type not in {"paper_buy", "paper_sell"}
    reason = execution_block_reason(db, block_paper_execution=block_paper)
    if not reason:
        return None
    logger.warning("Trade execution blocked (%s): %s", execution_type, reason)
    return {
        "status": "blocked_by_emergency_stop",
        "reason": reason,
        "execution_type": execution_type,
        "message": "Live trading is disabled. Audit/paper simulation mode only.",
    }


def disable_trading_for_audit(db: Session) -> dict[str, Any]:
    seed_dynamic_settings(db)
    for key, value, value_type in [
        ("live_trading_enabled", "false", "bool"),
        ("audit_mode", "true", "bool"),
        ("audit_mode_enabled", "true", "bool"),
        ("paper_trading_enabled", "true", "bool"),
        ("emergency_stop_trading", "true", "bool"),
        ("emergency_stop_enabled", "true", "bool"),
        ("portfolio_auto_execution_enabled", "false", "bool"),
        ("first_live_trade_approved", "false", "bool"),
        ("automation_run_portfolio_bot", "false", "bool"),
        ("portfolio_bot_auto_execute_paper_trades", "false", "bool"),
        ("portfolio_bot_enabled", "false", "bool"),
    ]:
        set_setting(db, key, value, value_type=value_type)
    settings = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
    settings.portfolio_bot_enabled = False
    settings.trading_mode = "paper_trading"
    settings.require_manual_buy_confirmation = True
    settings.require_manual_sell_confirmation = True
    db.add(settings)
    return safety_snapshot(db)


def mark_setting(db: Session, key: str, value: str, value_type: str = "bool", description: str | None = None) -> AutomationSetting:
    return set_setting(db, key, value, value_type=value_type, description=description)


def journal_trade_event(db: Session, payload: dict[str, Any]) -> TradeJournal:
    row = TradeJournal(
        date=payload.get("date") or datetime.now(UTC).replace(tzinfo=None),
        symbol=str(payload.get("symbol") or "").upper(),
        signal=payload.get("signal"),
        entry_zone=payload.get("entry_zone"),
        actual_entry=payload.get("actual_entry"),
        stop_loss=payload.get("stop_loss"),
        targets=payload.get("targets"),
        exit_price=payload.get("exit_price"),
        result=payload.get("result"),
        pnl=payload.get("pnl"),
        pnl_pct=payload.get("pnl_pct"),
        reason_for_entry=payload.get("reason_for_entry"),
        reason_for_exit=payload.get("reason_for_exit"),
        mistake_type=payload.get("mistake_type"),
        lesson_learned=payload.get("lesson_learned"),
    )
    db.add(row)
    return row
