from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.config import RESEARCH_DISCLAIMER, get_settings
from app.database import SessionLocal
from app.models import NotificationLog, TelegramSubscriber
from app.services.subscribers import active_alert_chat_ids
from app.services.telegram_bot import send_private_message_sync
from dashboard.ui_components import empty_state, professional_table, section_title, success_box, warning_box


def _mask_chat_id(value: str | int | None) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "****"
    return "*" * max(0, len(text) - 4) + text[-4:]


def _subscribers_frame(rows: list[TelegramSubscriber]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chat_id": _mask_chat_id(row.chat_id),
                "display_name": row.display_name or row.username or "-",
                "role": row.role,
                "active": row.is_active,
                "alerts": row.can_receive_alerts,
                "can_use_bot": row.can_use_bot,
                "last_status": row.last_message_status,
                "last_message_at": row.last_message_at,
                "last_error": row.last_message_error,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    )


def render() -> None:
    st.title("Telegram Bot Status")
    st.caption("Connection, subscribers, delivery history, and safe test sending. " + RESEARCH_DISCLAIMER)
    settings = get_settings()

    with SessionLocal() as db:
        subscribers = db.scalars(select(TelegramSubscriber).order_by(TelegramSubscriber.updated_at.desc())).all()
        active_ids = active_alert_chat_ids(db, settings=settings)
        latest = db.scalars(select(NotificationLog).order_by(NotificationLog.sent_at.desc()).limit(100)).all()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Token Configured", "YES" if settings.telegram_bot_token else "NO")
    c2.metric("Active Alert Users", len(active_ids))
    c3.metric("Registered Users", len(subscribers))
    c4.metric("TLS Verify", "ON" if settings.telegram_bot_verify_tls else "OFF")

    if st.button("Send Bot Test Message"):
        try:
            send_private_message_sync("EGX Telegram bot status test. Audit/paper mode remains active.")
            success_box("Test message sent to active subscribers.")
        except Exception as exc:
            warning_box(f"Telegram test failed: {exc}")

    section_title("Subscribers")
    df = _subscribers_frame(subscribers)
    professional_table(df, height=360) if not df.empty else empty_state("No subscribers have used /start or /subscribe yet.")

    section_title("Notification Deduplication Log")
    log_df = pd.DataFrame(
        [
            {
                "symbol": row.symbol,
                "type": row.notification_type,
                "recommendation": row.recommendation,
                "score": row.score,
                "delivery_status": row.delivery_status,
                "sent_at": row.sent_at,
                "source": row.source_module,
            }
            for row in latest
        ]
    )
    professional_table(log_df, height=420) if not log_df.empty else empty_state("No deduplicated notification sends have been logged yet.")
