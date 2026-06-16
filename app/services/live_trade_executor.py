from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE, RISK_NOTE
from app.data.market_data import latest_price
from app.intelligence.portfolio_bot import get_portfolio_settings, portfolio_value
from app.models import LiveTradeExecutionLog, PortfolioPosition, PortfolioTrade
from app.services.dynamic_settings import get_bool, get_float, get_int
from app.services.market_daily_evaluation import evaluate_daily_market
from app.services.trading_adapters import LiveTradingAdapter, PaperTradingAdapter, TradingAdapter
from app.services.trading_safety import safety_snapshot


CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)


@dataclass
class TradeValidation:
    allowed: bool
    status: str
    reasons: list[str]
    market: dict[str, Any]
    max_allowed_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reasons": self.reasons,
            "market": self.market,
            "max_allowed_value": self.max_allowed_value,
        }


def _day_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(CAIRO_TZ).replace(tzinfo=None)
    start = datetime(now.year, now.month, now.day)
    return start, start + timedelta(days=1)


def _open_position_value(db: Session) -> float:
    total = 0.0
    for pos in db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open")).all():
        price = latest_price(db, pos.symbol) or pos.current_price or pos.buy_price
        total += float(price or 0) * int(pos.quantity or 0)
    return total


def _daily_trade_counts(db: Session) -> tuple[int, int]:
    start, end = _day_bounds()
    total = int(
        db.scalar(
            select(func.count()).select_from(PortfolioTrade).where(
                PortfolioTrade.trade_date >= start,
                PortfolioTrade.trade_date < end,
            )
        )
        or 0
    )
    buys = int(
        db.scalar(
            select(func.count()).select_from(PortfolioTrade).where(
                PortfolioTrade.trade_type == "BUY",
                PortfolioTrade.trade_date >= start,
                PortfolioTrade.trade_date < end,
            )
        )
        or 0
    )
    return total, buys


def validate_live_trade(
    db: Session,
    *,
    symbol: str,
    action: str,
    quantity: int,
    price: float,
    recommendation: dict[str, Any] | None = None,
) -> TradeValidation:
    reasons: list[str] = []
    safety = safety_snapshot(db)
    action_norm = action.upper()
    recommendation = recommendation or {}
    market = evaluate_daily_market(db, persist=True)
    if safety.get("emergency_stop_trading") or get_bool(db, "emergency_stop_enabled", True):
        reasons.append("emergency stop enabled")
    if not safety.get("live_trading_enabled"):
        reasons.append("live trading disabled")
    if get_bool(db, "audit_mode", True) or get_bool(db, "audit_mode_enabled", True):
        reasons.append("audit mode enabled")
    if not get_bool(db, "portfolio_auto_execution_enabled", False):
        reasons.append("portfolio auto execution disabled")
    if get_bool(db, "require_manual_approval_for_first_live_trade", True) and not get_bool(db, "first_live_trade_approved", False):
        reasons.append("first live trade requires manual dashboard approval")
    if get_bool(db, "require_market_open_check", True) and market.get("market_status") != "open":
        reasons.append("market is not open")
    permission = str(market.get("trade_permission") or "DATA_INSUFFICIENT")
    if get_bool(db, "require_market_daily_score_check", True):
        min_market = get_float(db, "market_daily_min_score_to_trade", 60.0)
        if float(market.get("market_score") or 0) < min_market:
            reasons.append("market daily score below minimum")
        if action_norm == "BUY" and permission != "TRADE_ALLOWED":
            reasons.append(f"market permission blocks BUY ({permission})")
    confidence = float(recommendation.get("confidence") or recommendation.get("final_score") or 0)
    if action_norm == "BUY" and confidence < get_float(db, "min_confidence_to_trade", 75.0):
        reasons.append("confidence below minimum")
    signal = str(recommendation.get("signal") or recommendation.get("final_signal") or "").upper()
    if action_norm == "BUY" and signal not in {"BUY", "STRONG BUY", "CONDITIONAL BUY"}:
        reasons.append("recommendation is not a BUY stage")
    entry_low = recommendation.get("entry_zone_low")
    entry_high = recommendation.get("entry_zone_high")
    if action_norm == "BUY" and entry_low is not None and entry_high is not None:
        try:
            if not (float(entry_low) <= price <= float(entry_high)):
                reasons.append("price is outside entry zone")
        except Exception:
            reasons.append("entry zone is invalid")
    if action_norm == "BUY":
        if not recommendation.get("stop_loss"):
            reasons.append("stop loss missing")
        if not (recommendation.get("target_1") or recommendation.get("take_profit_1")):
            reasons.append("target missing")
        rr = recommendation.get("risk_reward")
        if rr is not None and float(rr or 0) < 1.8:
            reasons.append("risk/reward below 1.8")
    total_trades, buy_trades = _daily_trade_counts(db)
    if total_trades >= get_int(db, "max_daily_trades", 5, minimum=0):
        reasons.append("max daily trades reached")
    if action_norm == "BUY" and buy_trades >= get_int(db, "max_daily_buy_trades", 2, minimum=0):
        reasons.append("max daily BUY trades reached")
    settings = get_portfolio_settings(db)
    values = portfolio_value(db, settings)
    order_value = float(quantity) * float(price)
    max_position_value = values["total_value"] * (get_float(db, "max_position_size_percent", 20.0) / 100)
    max_total_exposure = values["total_value"] * (get_float(db, "max_total_portfolio_exposure_percent", 80.0) / 100)
    if action_norm == "BUY" and order_value > max_position_value:
        reasons.append("max position size exceeded")
    if action_norm == "BUY" and _open_position_value(db) + order_value > max_total_exposure:
        reasons.append("max total portfolio exposure exceeded")
    daily_loss_limit = get_float(db, "max_daily_loss_percent", 3.0)
    if float(safety.get("daily_loss_pct") or 0) >= daily_loss_limit:
        reasons.append("daily loss limit reached")
    status = "allowed" if not reasons else "blocked"
    return TradeValidation(not reasons, status, reasons, market, max_allowed_value=round(max_position_value, 2))


def _adapter_for_mode(mode: str, *, cash: float = 0.0) -> TradingAdapter:
    if mode == "paper":
        return PaperTradingAdapter(cash=cash)
    return LiveTradingAdapter()


def execute_trade(
    db: Session,
    *,
    symbol: str,
    action: str,
    quantity: int,
    price: float,
    recommendation: dict[str, Any] | None = None,
    mode: str = "live",
    adapter: TradingAdapter | None = None,
) -> dict[str, Any]:
    mode = mode.lower()
    validation = validate_live_trade(db, symbol=symbol, action=action, quantity=quantity, price=price, recommendation=recommendation)
    trade_id = f"trade_{datetime.now(CAIRO_TZ):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    broker_response: dict[str, Any] | None = None
    error_message = None
    status = "blocked"
    if validation.allowed:
        adapter = adapter or _adapter_for_mode(mode)
        if action.upper() == "BUY":
            broker_response = adapter.place_buy_order(symbol, quantity, price)
        else:
            broker_response = adapter.place_sell_order(symbol, quantity, price)
        status = str((broker_response or {}).get("status") or "submitted")
        if mode == "live" and status.startswith("blocked"):
            error_message = "Live broker adapter is not configured."
    else:
        error_message = "; ".join(validation.reasons)
    row = LiveTradeExecutionLog(
        trade_id=trade_id,
        cairo_timestamp=datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S Cairo"),
        symbol=symbol.upper(),
        action=action.upper(),
        mode=mode,
        quantity=int(quantity),
        price=float(price),
        order_value=float(quantity) * float(price),
        reason=(recommendation or {}).get("reason") or "; ".join(validation.reasons),
        recommendation_id=(recommendation or {}).get("recommendation_id"),
        market_score=validation.market.get("market_score"),
        market_regime=validation.market.get("market_regime"),
        execution_status=status,
        broker_response=broker_response or validation.to_dict(),
        error_message=error_message,
    )
    db.add(row)
    db.flush()
    return {
        "trade_id": trade_id,
        "status": status,
        "allowed": validation.allowed,
        "reasons": validation.reasons,
        "market": validation.market,
        "mode": mode,
        "risk_note": RISK_NOTE,
    }
