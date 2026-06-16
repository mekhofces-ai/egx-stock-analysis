
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import argparse
import os
import sqlite3

# ===== EGX SUBSCRIBERS PATCH =====
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SUBSCRIBERS_DB = DATA_DIR / "telegram_subscribers.db"

def _sub_conn():
    conn = sqlite3.connect(str(SUBSCRIBERS_DB), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn

def ensure_subscribers_table():
    with _sub_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_subscribers (
                chat_id TEXT PRIMARY KEY,
                chat_type TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_active INTEGER DEFAULT 1,
                subscribed_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

def register_subscriber(chat_id, chat_type=None, user=None):
    ensure_subscribers_table()
    user = user or {}
    now = datetime.now().isoformat(timespec="seconds")

    with _sub_conn() as conn:
        conn.execute("""
            INSERT INTO telegram_subscribers
            (chat_id, chat_type, username, first_name, last_name, is_active, subscribed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_type=excluded.chat_type,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                is_active=1,
                updated_at=excluded.updated_at
        """, (
            str(chat_id),
            str(chat_type or ""),
            str(user.get("username") or ""),
            str(user.get("first_name") or ""),
            str(user.get("last_name") or ""),
            now,
            now
        ))
        conn.commit()

def unregister_subscriber(chat_id):
    ensure_subscribers_table()
    with _sub_conn() as conn:
        conn.execute(
            "UPDATE telegram_subscribers SET is_active=0, updated_at=? WHERE chat_id=?",
            (datetime.now().isoformat(timespec="seconds"), str(chat_id))
        )
        conn.commit()

def get_subscribers():
    ensure_subscribers_table()
    with _sub_conn() as conn:
        rows = conn.execute("""
            SELECT chat_id, chat_type, username, first_name
            FROM telegram_subscribers
            WHERE is_active=1
            ORDER BY updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def get_alert_chat_ids():
    ids = []

    # Keep private admin chat too
    for value in [
        os.getenv("TELEGRAM_BOT_PRIVATE_CHAT_ID"),
        os.getenv("TELEGRAM_PRIVATE_CHAT_ID"),
        os.getenv("PRIVATE_CHAT_ID")
    ]:
        if value and str(value).strip():
            ids.append(str(value).strip())

    # Add comma-separated allowed chat IDs from .env
    allowed = os.getenv("TELEGRAM_BOT_ALLOWED_CHAT_IDS", "")
    for part in allowed.split(","):
        part = part.strip()
        if part:
            ids.append(part)

    # Add subscribers
    try:
        for sub in get_subscribers():
            ids.append(str(sub["chat_id"]))
    except Exception as e:
        print("Could not load subscribers:", e)

    # Unique while preserving order
    unique = []
    seen = set()
    for x in ids:
        if x not in seen:
            unique.append(x)
            seen.add(x)

    return unique

def broadcast_message(text):
    try:
        from app.services.subscribers import send_alert_to_subscribers_sync

        result = send_alert_to_subscribers_sync(text)
        return int(result.get("sent", 0))
    except Exception as exc:
        print("Subscriber broadcast failed, using legacy chat-id fallback:", exc)

    sent = 0
    for cid in get_alert_chat_ids():
        try:
            send_message(cid, text)
            sent += 1
        except Exception as exc:
            print("Broadcast failed to", cid, exc)
    return sent


def send_message(chat_id, text):
    from app.services.subscribers import send_message_to_chat_sync

    return send_message_to_chat_sync(chat_id, text)
# ===== END EGX SUBSCRIBERS PATCH =====



import logging
import ssl

import httpx

from app.config import get_settings
from app.database import init_db
from app.services.telegram_bot import create_bot_application


def _is_ssl_error(exc: Exception) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    text = str(exc).lower()
    return "certificate_verify_failed" in text or "self-signed certificate" in text or "certificate verify failed" in text


def _settings_after_tls_preflight(settings):
    if not settings.telegram_bot_token or not settings.telegram_bot_verify_tls:
        return settings
    try:
        with httpx.Client(timeout=10.0, verify=True) as client:
            response = client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
        response.raise_for_status()
        return settings
    except Exception as exc:
        if _is_ssl_error(exc):
            logging.getLogger(__name__).warning("Telegram bot TLS verification failed; retrying with verify=False.")
            return settings.model_copy(update={"telegram_bot_verify_tls": False})
        logging.getLogger(__name__).warning("Telegram bot preflight failed; polling will retry normally.")
        return settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EGX Telegram bot service.")
    parser.add_argument("--test", action="store_true", help="Validate bot/database setup without polling.")
    args = parser.parse_args()
    raw_settings = get_settings()
    logging.basicConfig(level=getattr(logging, raw_settings.log_level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = raw_settings if args.test else _settings_after_tls_preflight(raw_settings)
    init_db(seed=True)
    if args.test:
        from app.database import SessionLocal
        from app.models import TelegramSubscriber

        with SessionLocal() as db:
            subscriber_count = db.query(TelegramSubscriber).count()
        print(
            "Telegram bot test ok | "
            f"token_configured={'yes' if settings.telegram_bot_token else 'no'} | "
            f"subscribers={subscriber_count}"
        )
        return
    bot_application = create_bot_application(settings)
    if bot_application is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured or python-telegram-bot is not installed.")
    scheduler = None
    try:
        from app.services.scheduler import create_daily_report_scheduler

        scheduler = create_daily_report_scheduler()
        scheduler.start()
        logging.getLogger(__name__).info("Daily stock report scheduler started.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Daily stock report scheduler was not started: %s", exc)
    logging.getLogger(__name__).info("Telegram bot service starting.")
    try:
        bot_application.run_polling(drop_pending_updates=True)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)



# ===== EGX BROADCAST ALERT OVERRIDE =====
def _egx_split_chat_ids(value):
    if not value:
        return []
    return [x.strip() for x in str(value).replace(";", ",").split(",") if x.strip()]

def _egx_get_all_alert_chat_ids():
    try:
        from app.database import SessionLocal
        from app.services.subscribers import active_alert_chat_ids

        settings = get_settings()
        with SessionLocal() as db:
            return [str(chat_id) for chat_id in active_alert_chat_ids(db, settings=settings)]
    except Exception as exc:
        print("Could not read managed subscribers for broadcast:", exc)

    ids = []
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_BOT_PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_BOT_ALLOWED_CHAT_IDS"))

    unique = []
    seen = set()
    for cid in ids:
        if cid and cid not in seen:
            unique.append(cid)
            seen.add(cid)
    return unique

def _egx_broadcast_alert_message(text):
    sent = 0
    failed = 0

    for cid in _egx_get_all_alert_chat_ids():
        try:
            send_message(cid, text)
            sent += 1
        except Exception as e:
            failed += 1
            print("Broadcast alert failed to", cid, e)

    print("Broadcast alert result:", "sent=", sent, "failed=", failed)
    return sent

def send_system_buy_alerts(chat_id=None):
    try:
        from app.services.bot_analysis_adapter import get_unsent_buy_alerts, mark_alert_sent
    except Exception as e:
        print("Could not import alert adapter:", e)
        return 0

    try:
        alerts = get_unsent_buy_alerts(limit=10)
    except Exception as e:
        print("Could not load unsent alerts:", e)
        return 0

    if not alerts:
        print("No unsent alerts found.")
        return 0

    total_sent = 0

    for item in alerts:
        symbol = item.get("symbol") or "-"
        rec = item.get("recommendation") or "-"
        score = item.get("score") or item.get("final_score") or "-"
        entry = item.get("entry") or item.get("entry_price") or "-"
        target = item.get("target") or item.get("target_price") or "-"
        stop = item.get("stop_loss") or "-"
        reason = item.get("reason") or "-"

        text = (
            "🚨 EGX Opportunity Alert\n\n"
            f"Symbol: {symbol}\n"
            f"Recommendation: {rec}\n"
            f"Score: {score}\n"
            f"Entry: {entry}\n"
            f"Target: {target}\n"
            f"Stop Loss: {stop}\n\n"
            f"Reason:\n{reason}\n\n"
            "Risk Note: System-generated analysis, not financial advice."
        )

        total_sent += _egx_broadcast_alert_message(text)

        try:
            mark_alert_sent(
                item.get("alert_key"),
                symbol,
                rec
            )
        except Exception as e:
            print("Could not mark alert sent:", e)

    return total_sent
# ===== END EGX BROADCAST ALERT OVERRIDE =====


def _legacy_send_system_buy_alerts_unmanaged(chat_id=None):
    return send_system_buy_alerts(chat_id=chat_id)

def _unused_old_send_system_buy_alerts(chat_id=None):
    try:
        from app.database import SessionLocal
        from app.services.opportunity_engine import send_buy_alerts as send_opportunity_alerts
        from app.services.opportunity_engine import send_strategy_notifications

        with SessionLocal() as db:
            strategy_result = send_strategy_notifications(db)
            opportunity_result = send_opportunity_alerts(db)
        total_sent = int(strategy_result.get("sent", 0)) + int(opportunity_result.get("sent", 0))
        print(
            "System alerts completed:",
            "strategy_sent=",
            strategy_result.get("sent", 0),
            "opportunity_sent=",
            opportunity_result.get("sent", 0),
        )
        return total_sent
    except Exception as exc:
        print("Could not send system alerts:", exc)
        return 0


if __name__ == "__main__":
    main()
