from __future__ import annotations

import logging
import ssl
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    settings = _settings_after_tls_preflight(get_settings())
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    init_db(seed=True)
    bot_application = create_bot_application(settings)
    if bot_application is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured or python-telegram-bot is not installed.")
    logging.getLogger(__name__).info("Telegram bot service starting.")
    bot_application.run_polling()


if __name__ == "__main__":
    main()
