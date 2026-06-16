from __future__ import annotations

import asyncio
import argparse
from datetime import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.services.telegram_listener import TelegramListener

try:
    from telethon import TelegramClient
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Telethon is not installed: {exc}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Login Telegram reader session and optionally fetch active sources.")
    parser.add_argument("--reset-session", action="store_true", help="Back up the existing session files before login.")
    parser.add_argument("--skip-fetch", action="store_true", help="Only login; do not fetch sources after login.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")

    print("Telegram login helper")
    print("Type your phone number and Telegram code in this terminal only.")
    print("Do not paste the code into chat.")
    print()

    if args.reset_session:
        backup_dir = ROOT / "data" / "session_backups" / datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backed_up = []
        for path in ROOT.glob(f"{settings.telegram_session_name}.session*"):
            backup_dir.mkdir(parents=True, exist_ok=True)
            target = backup_dir / path.name
            path.replace(target)
            backed_up.append(str(target))
        if backed_up:
            print("Backed up existing session files:")
            for path in backed_up:
                print(f"- {path}")
        else:
            print("No existing session files found.")
        print()

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()
    me = await client.get_me()
    print(f"Logged in as: {getattr(me, 'username', None) or getattr(me, 'first_name', 'Telegram user')}")
    await client.disconnect()

    if args.skip_fetch:
        print("Login complete. Fetch skipped.")
        input("Press Enter to exit...")
        return

    print("Fetching active Telegram sources...")
    inserted = await TelegramListener(settings).fetch_once()
    print(f"Inserted messages: {inserted}")
    print("Done. You can close this window.")
    input("Press Enter to exit...")


if __name__ == "__main__":
    asyncio.run(main())
