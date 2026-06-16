
from __future__ import annotations
from pathlib import Path
from filelock import FileLock, Timeout

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import AppSetting, TelegramMessage, TelegramSource
from app.services.dynamic_settings import get_bool
from app.services.image_analyzer import download_telegram_image
from app.services.message_understanding import understand_telegram_message

# Cross-process lock for Telethon session.
# This prevents Dashboard + Automation from using the same .session file at the same time.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TELEGRAM_FETCH_LOCK = _PROJECT_ROOT / "data" / "telegram_fetch.lock"
_TELEGRAM_FETCH_LOCK.parent.mkdir(parents=True, exist_ok=True)



logger = logging.getLogger(__name__)
_fetch_lock = threading.Lock()

try:
    from telethon import TelegramClient, errors
except Exception:  # pragma: no cover - dependency can be absent during static checks
    TelegramClient = None
    errors = None


class TelegramListener:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(TelegramClient and self.settings.telegram_api_id and self.settings.telegram_api_hash)

    def _client(self) -> Any:
        if not self.is_configured:
            raise RuntimeError("Telethon is not configured. Set TELEGRAM_API_ID and TELEGRAM_API_HASH.")
        return TelegramClient(
            self.settings.telegram_session_name,
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )

    async def fetch_once(self) -> int:
        if not self.is_configured:
            logger.info("Skipping Telegram listener; TELEGRAM_API_ID/API_HASH not configured.")
            return 0
        if not _fetch_lock.acquire(blocking=False):
            logger.warning("Skipping Telegram fetch because another fetch is already running.")
            return 0
        inserted = 0
        try:
            async with self._client() as client:
                with SessionLocal() as db:
                    sources = db.scalars(select(TelegramSource).where(TelegramSource.is_active.is_(True))).all()
                    for source in sources:
                        try:
                            inserted += await self._fetch_source(client, db, source)
                        except Exception as exc:
                            logger.warning("Channel %s failed and was skipped: %s", source.username, exc)
                            db.rollback()
                    _set_listener_status(db, "ok", f"inserted={inserted}")
            return inserted
        except Exception as exc:
            with SessionLocal() as db:
                _set_listener_status(db, "failed", str(exc))
            raise
        finally:
            _fetch_lock.release()

    async def _fetch_source(self, client: Any, db: Session, source: TelegramSource) -> int:
        inserted = 0
        entity = await client.get_entity(source.username)
        source.title = getattr(entity, "title", None) or source.title or source.username
        source.source_type = "group" if getattr(entity, "megagroup", False) else "channel"
        max_seen_id = source.last_message_id or 0

        try:
            iterator = client.iter_messages(
                entity,
                min_id=source.last_message_id or 0,
                reverse=True,
                limit=self.settings.telegram_fetch_limit_per_channel,
            )
            async for message in iterator:
                message_id = int(getattr(message, "id", 0) or 0)
                if not message_id:
                    continue
                max_seen_id = max(max_seen_id, message_id)
                exists = db.scalar(
                    select(TelegramMessage.id).where(
                        TelegramMessage.source_id == source.id,
                        TelegramMessage.message_id == message_id,
                    )
                )
                if exists:
                    continue

                text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                image_path = None
                image_metadata = None
                media_type = None
                if getattr(message, "media", None):
                    file_obj = getattr(message, "file", None)
                    ext = (getattr(file_obj, "ext", "") or "").lower()
                    media_type = ext.lstrip(".") if ext else type(getattr(message, "media", None)).__name__
                    image_path, image_metadata = await download_telegram_image(client, message, source.username)

                telegram_message = TelegramMessage(
                    source_id=source.id,
                    message_id=message_id,
                    message_date=getattr(message, "date", None),
                    text=text,
                    channel_id=str(getattr(entity, "id", source.id) or source.id),
                    channel_name=source.title or source.username,
                    sender_id=str(getattr(message, "sender_id", "") or ""),
                    message_text=text,
                    media_type=media_type,
                    media_path=image_path,
                    raw_json=self._raw_message(message),
                    image_path=image_path,
                    image_metadata=image_metadata,
                )
                db.add(telegram_message)
                db.flush()
                try:
                    understand_telegram_message(db, telegram_message)
                    if image_path and get_bool(db, "telegram_analyze_images", False):
                        from app.services.image_analyzer import analyze_media_for_message

                        analyze_media_for_message(db, telegram_message)
                except Exception as exc:
                    logger.warning("Message understanding skipped for %s/%s: %s", source.username, message_id, exc)
                inserted += 1
        except Exception as exc:
            if errors and isinstance(exc, getattr(errors, "FloodWaitError", ())):
                wait_seconds = int(getattr(exc, "seconds", 60))
                logger.warning("Telegram flood wait for %s seconds while reading %s.", wait_seconds, source.username)
                await asyncio.sleep(wait_seconds)
            else:
                raise
        finally:
            source.last_message_id = max_seen_id
            db.commit()
        return inserted

    def _raw_message(self, message: Any) -> dict[str, Any]:
        date = getattr(message, "date", None)
        return {
            "id": getattr(message, "id", None),
            "date": date.isoformat() if isinstance(date, datetime) else None,
            "has_media": bool(getattr(message, "media", None)),
            "post_author": getattr(message, "post_author", None),
        }


def fetch_active_channels_once():
    """
    Run Telegram ingestion with a cross-process lock.

    Reason:
    Telethon uses a SQLite .session file.
    If Streamlit dashboard, automation_runner, and manual fetch run together,
    Telethon can raise: sqlite3.OperationalError: database is locked.

    This lock makes sure only one process uses the Telegram session at a time.
    """
    lock = FileLock(str(_TELEGRAM_FETCH_LOCK), timeout=2)

    try:
        with lock:
            return asyncio.run(TelegramListener().fetch_once())

    except Timeout:
        print("Telegram fetch skipped: another process is already using the Telegram session.")
        return 0

def _set_listener_status(db: Session, status: str, details: str) -> None:
    value = f"{datetime.utcnow().isoformat()}|{status}|{details[:500]}"
    existing = db.scalar(select(AppSetting).where(AppSetting.key == "telegram_listener_status"))
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key="telegram_listener_status", value=value))
    db.commit()
