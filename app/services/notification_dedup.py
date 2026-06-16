from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NotificationLog

logger = logging.getLogger(__name__)

CAIRO_TZ = ZoneInfo("Africa/Cairo")

DEFAULT_COOLDOWN_MINUTES = 120
DEFAULT_MAX_PER_STOCK_PER_DAY = 1
DEFAULT_MAX_BUY_ALERTS_PER_DAY = 20


def _trading_date() -> str:
    return datetime.now(CAIRO_TZ).strftime("%Y-%m-%d")


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def compute_hash(symbol: str, recommendation: str, entry_zone: str | None = None,
                 target: str | None = None, stop_loss: str | None = None,
                 trading_date: str | None = None) -> str:
    td = trading_date or _trading_date()
    sym = symbol.strip().upper()
    rec = recommendation.strip().upper() if recommendation else ""
    raw = f"{sym}:{rec}:{_normalize(entry_zone)}:{_normalize(target)}:{_normalize(stop_loss)}:{td}"
    return hashlib.sha256(raw.encode()).hexdigest()


def should_send(db: Session, symbol: str, recommendation: str, notification_type: str,
                entry_zone: str | None = None, target: str | None = None,
                stop_loss: str | None = None,
                cooldown_minutes: int | None = None,
                max_per_stock_per_day: int = DEFAULT_MAX_PER_STOCK_PER_DAY,
                max_buy_alerts_per_day: int = DEFAULT_MAX_BUY_ALERTS_PER_DAY) -> tuple[bool, str]:
    settings = get_settings()
    if not settings.telegram_alert_enabled:
        return False, "Telegram alerts disabled"

    cd = cooldown_minutes or DEFAULT_COOLDOWN_MINUTES
    ntype = notification_type[:64]
    trading_dt = _trading_date()
    symbol_norm = symbol.strip().upper()
    recommendation_norm = recommendation.strip().upper() if recommendation else ""
    h = compute_hash(symbol, recommendation, entry_zone, target, stop_loss, trading_dt)

    day_start = datetime.now(CAIRO_TZ).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    day_end = day_start + timedelta(days=1)

    same_stock_today = db.execute(
        select(NotificationLog).where(
            NotificationLog.symbol == symbol_norm,
            NotificationLog.notification_type == ntype,
            NotificationLog.recommendation == recommendation_norm,
            NotificationLog.sent_at >= day_start,
            NotificationLog.sent_at < day_end,
        )
    ).first()
    if same_stock_today:
        logger.info("Daily duplicate skip %s %s %s", symbol_norm, recommendation_norm, ntype)
        return False, "Same stock + same recommendation already sent today"

    if max_per_stock_per_day > 0:
        stock_count = db.scalar(
            select(func.count()).select_from(NotificationLog).where(
                NotificationLog.symbol == symbol_norm,
                NotificationLog.sent_at >= day_start,
                NotificationLog.sent_at < day_end,
            )
        ) or 0
        if int(stock_count) >= max_per_stock_per_day:
            return False, f"Daily stock alert limit reached ({max_per_stock_per_day})"

    if recommendation_norm in {"BUY", "STRONG BUY", "CONDITIONAL BUY"} and max_buy_alerts_per_day > 0:
        buy_count = db.scalar(
            select(func.count()).select_from(NotificationLog).where(
                NotificationLog.recommendation.in_(["BUY", "STRONG BUY", "CONDITIONAL BUY"]),
                NotificationLog.sent_at >= day_start,
                NotificationLog.sent_at < day_end,
            )
        ) or 0
        if int(buy_count) >= max_buy_alerts_per_day:
            return False, f"Daily BUY alert limit reached ({max_buy_alerts_per_day})"

    existing = db.execute(
        select(NotificationLog).where(
            NotificationLog.notification_hash == h,
            NotificationLog.notification_type == ntype,
        )
    ).scalar()
    if existing:
        logger.info("Hash skip %s %s %s: already sent at %s", symbol, recommendation, ntype, existing.sent_at)
        return False, "Duplicate notification hash already sent"

    cooldown_at = (datetime.now(CAIRO_TZ) - timedelta(minutes=cd)).replace(tzinfo=None)
    recent = db.execute(
        select(NotificationLog).where(
            NotificationLog.symbol == symbol,
            NotificationLog.notification_type == ntype,
            NotificationLog.recommendation == recommendation,
            NotificationLog.sent_at >= cooldown_at,
        ).order_by(NotificationLog.sent_at.desc())
    ).first()
    if recent is not None:
        logger.info("Cooldown skip %s %s %s: sent %s within %d min", symbol, recommendation, ntype, recent[0] if isinstance(recent, tuple) else recent.sent_at, cd)
        return False, f"Cooldown active (last sent at {recent[0] if isinstance(recent, tuple) else recent.sent_at})"

    return True, ""


def mark_sent(db: Session, symbol: str, recommendation: str, notification_type: str,
              source_module: str = "", score: float | None = None,
              entry_zone: str | None = None, target: str | None = None,
              stop_loss: str | None = None) -> NotificationLog:
    ntype = notification_type[:64]
    trading_dt = _trading_date()
    h = compute_hash(symbol, recommendation, entry_zone, target, stop_loss, trading_dt)
    log = NotificationLog(
        notification_hash=h,
        symbol=symbol.strip().upper(),
        notification_type=ntype,
        recommendation=recommendation.strip().upper() if recommendation else "",
        score=score,
        entry_zone=_normalize(entry_zone)[:255],
        target=_normalize(target)[:255],
        stop_loss=_normalize(stop_loss)[:255],
        source_module=source_module[:100],
        cooldown_applied=False,
        delivery_status="sent",
        sent_at=datetime.now(CAIRO_TZ).replace(tzinfo=None),
    )
    db.add(log)
    db.flush()
    logger.info("Marked sent: %s %s %s (hash=%s)", symbol, recommendation, ntype, h[:12])
    return log


def check_and_mark(db: Session, symbol: str, recommendation: str, notification_type: str,
                   source_module: str = "", score: float | None = None,
                   entry_zone: str | None = None, target: str | None = None,
                   stop_loss: str | None = None,
                   cooldown_minutes: int | None = None,
                   max_per_stock_per_day: int = DEFAULT_MAX_PER_STOCK_PER_DAY,
                   max_buy_alerts_per_day: int = DEFAULT_MAX_BUY_ALERTS_PER_DAY) -> tuple[bool, str]:
    ok, reason = should_send(db, symbol, recommendation, notification_type,
                             entry_zone, target, stop_loss, cooldown_minutes,
                             max_per_stock_per_day=max_per_stock_per_day,
                             max_buy_alerts_per_day=max_buy_alerts_per_day)
    if not ok:
        return False, reason
    mark_sent(db, symbol, recommendation, notification_type, source_module,
              score, entry_zone, target, stop_loss)
    return True, "Sent"
