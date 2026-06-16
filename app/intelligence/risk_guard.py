from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence.portfolio_bot import portfolio_value
from app.models import PortfolioSetting, PortfolioTrade


def _loss_pct(trades: list[PortfolioTrade], base_value: float) -> float:
    losses = sum(float(row.profit_loss or 0) for row in trades if float(row.profit_loss or 0) < 0)
    return abs(losses) / base_value * 100 if base_value else 0.0


def consecutive_losses(trades: list[PortfolioTrade]) -> int:
    count = 0
    for row in trades:
        if (row.profit_loss or 0) < 0:
            count += 1
        else:
            break
    return count


def risk_guard_status(db: Session, settings: PortfolioSetting | None = None) -> dict[str, Any]:
    settings = settings or db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc()))
    if not settings:
        return {"allowed": False, "reasons": ["portfolio settings missing"], "status": "blocked"}
    values = portfolio_value(db, settings)
    now = datetime.utcnow()
    today = now.date()
    daily_trades = db.scalars(select(PortfolioTrade).where(PortfolioTrade.trade_date >= datetime.combine(today, datetime.min.time()))).all()
    weekly_trades = db.scalars(select(PortfolioTrade).where(PortfolioTrade.trade_date >= now - timedelta(days=7))).all()
    recent_sells = db.scalars(select(PortfolioTrade).where(PortfolioTrade.trade_type == "SELL").order_by(PortfolioTrade.trade_date.desc()).limit(20)).all()
    total_drawdown = max(0.0, (settings.initial_cash - values["total_value"]) / settings.initial_cash * 100) if settings.initial_cash else 0.0
    daily_loss = _loss_pct(daily_trades, values["total_value"])
    weekly_loss = _loss_pct(weekly_trades, values["total_value"])
    losing_streak = consecutive_losses(recent_sells)
    reasons: list[str] = []
    if daily_loss >= settings.max_daily_loss_pct:
        reasons.append("daily portfolio loss exceeded")
    if weekly_loss >= settings.max_weekly_loss_pct:
        reasons.append("weekly portfolio loss exceeded")
    if total_drawdown >= settings.max_drawdown_pct:
        reasons.append("max drawdown exceeded")
    if losing_streak >= settings.max_consecutive_losses:
        reasons.append("too many losing trades in a row")
    return {
        "allowed": not reasons,
        "status": "ok" if not reasons else "blocked",
        "reasons": reasons,
        "daily_loss_pct": round(daily_loss, 2),
        "weekly_loss_pct": round(weekly_loss, 2),
        "drawdown_pct": round(total_drawdown, 2),
        "consecutive_losses": losing_streak,
        "portfolio": values,
    }

