from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings, Settings
from app.database import sqlite_write_lock
from app.models import NotificationLog
from app.services.pre_trade_validator import (
    ACTION_STAGES,
    NoTradeFilterResult,
    PreTradeResult,
    RecommendationStage,
    check_alert_policy,
    check_no_trade_filters,
    classify_stage,
    journal_recommendation,
    market_regime_downgrade,
    pre_trade_validate,
)
# import send_private_message_sync lazily to avoid circular import

logger = logging.getLogger(__name__)
CAIRO_TZ = ZoneInfo("Africa/Cairo")

DEFAULT_COOLDOWN_MINUTES = 120
MAX_BUY_ALERTS_PER_DAY = 5
MAX_ALERTS_PER_STOCK_PER_DAY = 2


@dataclass
class StockAlertResult:
    sent: bool = False
    blocked: bool = False
    reason: str = ""
    stage: str = ""
    notification_log_id: int | None = None


def _trading_date() -> str:
    return datetime.now(CAIRO_TZ).strftime("%Y-%m-%d")


def _normalize(value: float | None, precision: int = 1) -> str:
    if value is None:
        return ""
    return f"{round(value, precision):.{precision}f}"


def _bucketed(val: str | float | None, bucket_size: float = 5.0) -> str:
    """Round numerical values into buckets to reduce hash sensitivity."""
    if val is None or val == "" or val == "None":
        return ""
    try:
        num = float(val)
        if num == 0:
            return ""
        bucket = round(num / bucket_size) * bucket_size
        return f"{bucket:.0f}"
    except (ValueError, TypeError):
        return str(val).strip().upper()


def compute_alert_hash(symbol: str, stage: str, recommendation: str,
                       entry_zone: str | float | None = "",
                       target: str | float | None = "",
                       stop_loss: str | float | None = "",
                       trading_date: str | None = None) -> str:
    td = trading_date or _trading_date()
    sym = symbol.strip().upper() if symbol else ""
    stg = stage.strip().upper() if stage else ""
    rec = recommendation.strip().upper() if recommendation else ""
    # Bucket prices into $5 ranges to prevent tiny changes from changing hash
    ez = _bucketed(entry_zone, 5.0)
    tg = _bucketed(target, 5.0)
    sl = _bucketed(stop_loss, 5.0)
    raw = f"{sym}:{stg}:{rec}:{ez}:{tg}:{sl}:{td}"
    return hashlib.sha256(raw.encode()).hexdigest()


def is_trading_session() -> tuple[bool, str]:
    now = datetime.now(CAIRO_TZ)
    # EGX trading: Sunday-Thursday, 10:30-14:30 Cairo
    if now.weekday() >= 5:
        return False, "Weekend (EGX closed Fri-Sat)"
    market_open = now.replace(hour=10, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=14, minute=30, second=0, microsecond=0)
    if now < market_open:
        return False, f"Before EGX open ({market_open.strftime('%H:%M')} Cairo)"
    if now > market_close:
        return False, f"After EGX close ({market_close.strftime('%H:%M')} Cairo)"
    return True, ""


def is_muted_symbol(symbol: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    muted = getattr(settings, "muted_symbols", "") or ""
    return symbol.strip().upper() in [s.strip().upper() for s in muted.split(",") if s.strip()]


def send_stock_alert(
    db: Session,
    symbol: str,
    recommendation: str,
    entry_price: float | None = None,
    target_price: float | None = None,
    stop_loss: float | None = None,
    score: float | None = None,
    source_module: str = "",
    message_text: str | None = None,
    row_data: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> StockAlertResult:
    settings = settings or get_settings()

    # 1. Global kill switch
    if not getattr(settings, "enable_stock_alerts", True):
        logger.info("Blocked %s: stock alerts globally disabled", symbol)
        return StockAlertResult(blocked=True, reason="Stock alerts globally disabled")

    # 2. Muted symbols
    if is_muted_symbol(symbol, settings):
        logger.info("Blocked %s: symbol is muted", symbol)
        return StockAlertResult(blocked=True, reason=f"Symbol {symbol} is muted")

    # 3. Pre-trade validation
    pv_result = pre_trade_validate(db, symbol, row_data or {})
    if not pv_result.passed:
        reasons = "; ".join(pv_result.reasons[:3])
        logger.info("Blocked %s: pre-trade validation failed: %s", symbol, reasons)
        return StockAlertResult(blocked=True, reason=reasons, stage=pv_result.stage.value)

    # 4. Stage check — only actionable stages trigger Telegram
    if pv_result.stage not in ACTION_STAGES:
        logger.info("Blocked %s: stage %s not actionable", symbol, pv_result.stage.value)
        return StockAlertResult(blocked=True, reason=f"Stage {pv_result.stage.value} does not send Telegram alerts",
                                stage=pv_result.stage.value)

    # 5. Alert policy (max per day, per stock)
    ok, policy_reason = check_alert_policy(db, symbol, pv_result.stage)
    if not ok:
        logger.info("Blocked %s: policy limit: %s", symbol, policy_reason)
        return StockAlertResult(blocked=True, reason=policy_reason, stage=pv_result.stage.value)

    # 6. Compute normalized hash with bucketed values
    entry_norm = _bucketed(entry_price, 5.0)
    target_norm = _bucketed(target_price, 5.0)
    stop_norm = _bucketed(stop_loss, 5.0)
    trading_dt = _trading_date()
    h = compute_alert_hash(symbol, pv_result.stage.value, recommendation,
                           entry_norm, target_norm, stop_norm, trading_dt)

    # 7. Hash dedup + cooldown within a single write lock
    ntype = source_module[:64] if source_module else "stock_alert"
    with sqlite_write_lock():
        # Dedup: check hash uniqueness
        dup = db.execute(
            select(NotificationLog).where(NotificationLog.notification_hash == h)
        ).scalar()
        if dup:
            logger.info("Blocked %s: duplicate hash %s already sent at %s",
                        symbol, h[:12], dup.sent_at)
            return StockAlertResult(blocked=True, reason="Duplicate notification (hash match)")

        # Cooldown: same symbol+stage+rec not within cooldown
        cd = getattr(settings, "notification_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
        cooldown_at = datetime.now(CAIRO_TZ) - timedelta(minutes=cd)
        recent = db.execute(
            select(NotificationLog).where(
                NotificationLog.symbol == symbol,
                NotificationLog.notification_type == ntype,
                NotificationLog.recommendation == recommendation,
                NotificationLog.sent_at >= cooldown_at,
            ).order_by(NotificationLog.sent_at.desc())
        ).first()
        if recent is not None:
            logger.info("Blocked %s: cooldown active (last sent at %s)", symbol, recent[0] if isinstance(recent, tuple) else recent.sent_at)
            return StockAlertResult(blocked=True, reason="Cooldown active")

        # 8. Create notification log entry (pre-insert before send)
        log = NotificationLog(
            notification_hash=h,
            symbol=symbol.strip().upper(),
            notification_type=ntype,
            recommendation=recommendation.strip().upper() if recommendation else "",
            score=score,
            entry_zone=entry_norm,
            target=target_norm,
            stop_loss=stop_norm,
            source_module=source_module[:100],
            cooldown_applied=False,
        )
        db.add(log)
        db.flush()  # Assigns ID, raises IntegrityError if hash unique constraint violated
        log_id = log.id

    # 9. Send the message
    if message_text:
        try:
            from app.services.telegram_bot import send_private_message_sync
            send_private_message_sync(message_text, settings=settings)
            logger.info("Sent alert for %s (%s, score=%s, stage=%s)",
                        symbol, recommendation, score, pv_result.stage.value)
        except Exception as exc:
            logger.error("Failed to send alert for %s: %s", symbol, exc)
            # Still keep the notification log (blocked at delivery)
            with sqlite_write_lock():
                log.delivery_status = "failed"
                db.commit()
            return StockAlertResult(blocked=True, reason=f"Send failed: {exc}", stage=pv_result.stage.value)

    # 10. Commit the notification log as sent
    with sqlite_write_lock():
        log.delivery_status = "sent"
        db.commit()

    # 11. Record in trading journal
    journal_recommendation(db, symbol, pv_result, row_data or {})

    return StockAlertResult(sent=True, stage=pv_result.stage.value, notification_log_id=log_id)


# ---- Adapter functions for backward compatibility ----

def check_and_send_stock_alert(
    db: Session,
    symbol: str,
    recommendation: str,
    score: float | None,
    entry_price: float | None = None,
    target_price: float | None = None,
    stop_loss: float | None = None,
    source_module: str = "",
    message_text: str | None = None,
    row_data: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> tuple[bool, str, StockAlertResult]:
    """Check then send. Returns (should_proceed, skip_reason, result)."""
    result = send_stock_alert(
        db=db, symbol=symbol, recommendation=recommendation,
        entry_price=entry_price, target_price=target_price,
        stop_loss=stop_loss, score=score, source_module=source_module,
        message_text=message_text, row_data=row_data, settings=settings,
    )
    if result.blocked:
        return False, result.reason, result
    return True, "", result


def close_trading_session_if_needed(db: Session) -> None:
    """Log end-of-day info."""
    db.commit()
