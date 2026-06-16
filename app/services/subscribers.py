from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, sqlite_write_lock
from app.models import TelegramSubscriber


logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

try:
    from telegram import Bot
    from telegram.request import HTTPXRequest
except Exception:  # pragma: no cover - optional dependency
    Bot = None
    HTTPXRequest = None


def _chat_id(value: int | str | None) -> str:
    return str(value or "").strip()


def _display_name(username: str | None, first_name: str | None, last_name: str | None) -> str:
    parts = [part for part in [first_name, last_name] if part]
    label = " ".join(parts).strip()
    if username:
        handle = username if str(username).startswith("@") else f"@{username}"
        label = f"{label} {handle}".strip()
    return label or "Telegram user"


def _is_configured_admin(chat_id: str, settings: Settings) -> bool:
    try:
        return int(chat_id) in settings.allowed_chat_ids
    except Exception:
        return False


def ensure_admin_subscriber(db: Session, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.telegram_bot_private_chat_id is None:
        return
    chat_id = _chat_id(settings.telegram_bot_private_chat_id)
    subscriber = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == chat_id))
    if subscriber:
        subscriber.role = "admin"
        subscriber.is_active = True
        subscriber.can_use_bot = True
        subscriber.can_receive_alerts = True
        subscriber.updated_at = datetime.utcnow()
        return
    db.add(
        TelegramSubscriber(
            chat_id=chat_id,
            chat_type="private",
            display_name="Configured admin",
            role="admin",
            is_active=True,
            can_use_bot=True,
            can_receive_alerts=True,
            notes="Seeded from private chat id setting.",
        )
    )


def register_or_update_subscriber(
    db: Session,
    chat_id: int | str,
    chat_type: str | None = None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    display_name: str | None = None,
    role: str | None = None,
    activate: bool = True,
    can_receive_alerts: bool | None = None,
    can_use_bot: bool | None = None,
    settings: Settings | None = None,
) -> TelegramSubscriber:
    settings = settings or get_settings()
    ensure_admin_subscriber(db, settings=settings)
    normalized = _chat_id(chat_id)
    subscriber = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == normalized))
    now = datetime.utcnow()
    if subscriber is None:
        subscriber = TelegramSubscriber(
            chat_id=normalized,
            chat_type=chat_type,
            username=username,
            first_name=first_name,
            last_name=last_name,
            display_name=display_name or _display_name(username, first_name, last_name),
            role=role or ("admin" if _is_configured_admin(normalized, settings) else "user"),
            is_active=activate,
            can_receive_alerts=True if can_receive_alerts is None else bool(can_receive_alerts),
            can_use_bot=True if can_use_bot is None else bool(can_use_bot),
            subscribed_at=now,
            updated_at=now,
        )
        db.add(subscriber)
    else:
        subscriber.chat_type = chat_type or subscriber.chat_type
        subscriber.username = username if username is not None else subscriber.username
        subscriber.first_name = first_name if first_name is not None else subscriber.first_name
        subscriber.last_name = last_name if last_name is not None else subscriber.last_name
        subscriber.display_name = display_name or subscriber.display_name or _display_name(username, first_name, last_name)
        if role:
            subscriber.role = role
        if activate:
            subscriber.is_active = True
        if can_receive_alerts is not None:
            subscriber.can_receive_alerts = bool(can_receive_alerts)
        if can_use_bot is not None:
            subscriber.can_use_bot = bool(can_use_bot)
        subscriber.updated_at = now
    if _is_configured_admin(normalized, settings):
        subscriber.role = "admin"
        subscriber.can_use_bot = True
        subscriber.is_active = True
    return subscriber


def register_from_update(db: Session, update: Any, settings: Settings | None = None, activate: bool = True) -> TelegramSubscriber | None:
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return None
    user = getattr(update, "effective_user", None)
    return register_or_update_subscriber(
        db,
        chat_id=getattr(chat, "id", None),
        chat_type=getattr(chat, "type", None),
        username=getattr(user, "username", None) or getattr(chat, "username", None),
        first_name=getattr(user, "first_name", None) or getattr(chat, "first_name", None),
        last_name=getattr(user, "last_name", None),
        activate=activate,
        settings=settings,
    )


def set_subscription_flags(
    db: Session,
    chat_id: int | str,
    *,
    is_active: bool | None = None,
    can_receive_alerts: bool | None = None,
    can_use_bot: bool | None = None,
) -> TelegramSubscriber | None:
    subscriber = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == _chat_id(chat_id)))
    if not subscriber:
        return None
    if is_active is not None:
        subscriber.is_active = bool(is_active)
    if can_receive_alerts is not None:
        subscriber.can_receive_alerts = bool(can_receive_alerts)
    if can_use_bot is not None:
        subscriber.can_use_bot = bool(can_use_bot)
    subscriber.updated_at = datetime.utcnow()
    return subscriber


def is_admin(db: Session, chat_id: int | str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    ensure_admin_subscriber(db, settings=settings)
    normalized = _chat_id(chat_id)
    row = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == normalized))
    if row and row.role == "admin" and row.is_active:
        return True
    try:
        return int(normalized) in settings.allowed_chat_ids
    except Exception:
        return False


def can_use_bot(db: Session, chat_id: int | str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    normalized = _chat_id(chat_id)
    row = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == normalized))
    if row:
        return bool(row.is_active and row.can_use_bot)
    try:
        return int(normalized) in settings.allowed_chat_ids
    except Exception:
        return False


def list_subscribers(db: Session, include_inactive: bool = True) -> list[TelegramSubscriber]:
    stmt = select(TelegramSubscriber).order_by(TelegramSubscriber.updated_at.desc())
    if not include_inactive:
        stmt = stmt.where(TelegramSubscriber.is_active.is_(True))
    return db.scalars(stmt).all()


def active_alert_chat_ids(db: Session, settings: Settings | None = None) -> list[int]:
    settings = settings or get_settings()
    ensure_admin_subscriber(db, settings=settings)
    rows = db.scalars(
        select(TelegramSubscriber).where(
            TelegramSubscriber.is_active.is_(True),
            TelegramSubscriber.can_receive_alerts.is_(True),
        )
    ).all()
    inactive_ids = {
        str(row.chat_id)
        for row in db.scalars(select(TelegramSubscriber).where(TelegramSubscriber.is_active.is_(False))).all()
    }
    ids: list[int] = []
    for row in rows:
        try:
            ids.append(int(row.chat_id))
        except Exception:
            continue
    for chat_id in settings.allowed_chat_ids:
        if str(chat_id) not in inactive_ids and chat_id not in ids:
            ids.append(int(chat_id))
    unique: list[int] = []
    seen: set[int] = set()
    for chat_id in ids:
        if chat_id not in seen:
            unique.append(chat_id)
            seen.add(chat_id)
    return unique


def format_profile(subscriber: TelegramSubscriber | None) -> str:
    if not subscriber:
        return "No Telegram profile is registered for this chat yet."
    allowed = subscriber.allowed_symbols or "all"
    return (
        "Telegram Profile\n"
        f"Display name: {subscriber.display_name or subscriber.username or '-'}\n"
        f"Role: {subscriber.role}\n"
        f"Active: {'yes' if subscriber.is_active else 'no'}\n"
        f"Can use bot: {'yes' if subscriber.can_use_bot else 'no'}\n"
        f"Receives alerts: {'yes' if subscriber.can_receive_alerts else 'no'}\n"
        f"Allowed symbols: {allowed}\n"
        f"Risk Note: {RISK_NOTE}"
    )


def format_subscribers(rows: list[TelegramSubscriber], limit: int = 50) -> str:
    if not rows:
        return "No Telegram subscribers are registered yet."
    lines = ["Telegram Subscribers", ""]
    for row in rows[:limit]:
        lines.append(
            f"- {row.chat_id} | {row.display_name or row.username or '-'} | {row.role} | "
            f"active={'yes' if row.is_active else 'no'} | alerts={'yes' if row.can_receive_alerts else 'no'}"
        )
    if len(rows) > limit:
        lines.append(f"...and {len(rows) - limit} more")
    lines.append(f"Risk Note: {RISK_NOTE}")
    return "\n".join(lines)


def _telegram_request(settings: Settings):
    if HTTPXRequest is None:
        return None
    kwargs = {"verify": False} if not settings.telegram_bot_verify_tls else None
    return HTTPXRequest(httpx_kwargs=kwargs) if kwargs else HTTPXRequest()


async def send_message_to_chat(chat_id: int | str, text: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if RISK_NOTE not in text:
        text = f"{text.rstrip()}\n\nRisk Note: {RISK_NOTE}"
    if not settings.telegram_bot_token or Bot is None:
        raise RuntimeError("Telegram bot token is not configured or python-telegram-bot is unavailable.")
    bot = Bot(settings.telegram_bot_token, request=_telegram_request(settings))
    await bot.send_message(chat_id=int(chat_id), text=text)
    return True


def send_message_to_chat_sync(chat_id: int | str, text: str, settings: Settings | None = None) -> bool:
    return asyncio.run(send_message_to_chat(chat_id, text, settings=settings))


async def send_alert_to_subscribers(text: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if RISK_NOTE not in text:
        text = f"{text.rstrip()}\n\nRisk Note: {RISK_NOTE}"
    result = {"configured": bool(settings.telegram_bot_token and Bot), "eligible": 0, "sent": 0, "failed": 0, "errors": []}
    if not result["configured"]:
        return result
    with SessionLocal() as db:
        chat_ids = active_alert_chat_ids(db, settings=settings)
        result["eligible"] = len(chat_ids)
    for chat_id in chat_ids:
        try:
            await send_message_to_chat(chat_id, text, settings=settings)
            _mark_send_status(chat_id, "ok")
            result["sent"] += 1
        except Exception as exc:
            _mark_send_status(chat_id, "failed", str(exc))
            logger.warning("Could not send subscriber alert to chat %s: %s", chat_id, exc)
            result["failed"] += 1
            result["errors"].append(f"{chat_id}: {exc}")
    return result


def send_alert_to_subscribers_sync(text: str, settings: Settings | None = None) -> dict[str, Any]:
    return asyncio.run(send_alert_to_subscribers(text, settings=settings))


def _mark_send_status(chat_id: int | str, status: str, error: str | None = None) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == _chat_id(chat_id)))
        if row:
            row.last_message_status = status
            row.last_message_error = error
            row.last_message_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            with sqlite_write_lock():
                db.commit()
