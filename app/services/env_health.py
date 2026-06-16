from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings


SECRET_WORDS = ("TOKEN", "HASH", "PASSWORD", "SECRET", "COOKIE", "SESSION", "AUTH", "KEY")

GROUPS: dict[str, list[str]] = {
    "telegram_bot_token": ["TELEGRAM_BOT_TOKEN", "BOT_TOKEN"],
    "telegram_private_chat": ["TELEGRAM_BOT_PRIVATE_CHAT_ID", "TELEGRAM_PRIVATE_CHAT_ID", "PRIVATE_CHAT_ID"],
    "telegram_api_id": ["TELEGRAM_API_ID", "API_ID"],
    "telegram_api_hash": ["TELEGRAM_API_HASH", "API_HASH"],
    "database": ["EGX_DATABASE_URL"],
    "automation": ["AUTOMATION_ENABLED", "AUTOMATION_INTERVAL_SECONDS"],
    "market_data": ["MARKET_DATA_PROVIDER_PRIORITY", "MARKET_DATA_PROVIDER"],
}


def _is_secret_key(key: str) -> bool:
    return any(word in key.upper() for word in SECRET_WORDS)


def parse_env(path: str | Path = ".env") -> dict[str, Any]:
    env_path = Path(path)
    rows: list[dict[str, Any]] = []
    duplicates: dict[str, list[int]] = {}
    values: dict[str, str] = {}
    if not env_path.exists():
        return {"exists": False, "rows": [], "duplicates": {}, "values": {}}
    for line_no, line in enumerate(env_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        rows.append(
            {
                "line": line_no,
                "key": key,
                "is_secret": _is_secret_key(key),
                "is_set": bool(value),
            }
        )
        values[key] = value
        duplicates.setdefault(key, []).append(line_no)
    duplicates = {key: lines for key, lines in duplicates.items() if len(lines) > 1}
    return {"exists": True, "rows": rows, "duplicates": duplicates, "values": values}


def build_env_health(path: str | Path = ".env") -> dict[str, Any]:
    parsed = parse_env(path)
    values = parsed.get("values") or {}
    group_status: dict[str, dict[str, Any]] = {}
    for group, keys in GROUPS.items():
        present = [key for key in keys if values.get(key)]
        group_status[group] = {
            "ok": bool(present),
            "keys_present": present,
            "accepted_aliases": keys,
        }
    settings = get_settings()
    warnings: list[str] = []
    if parsed.get("duplicates"):
        warnings.append("Duplicate keys exist; the last value usually wins.")
    if not settings.telegram_bot_token:
        warnings.append("Telegram bot token is not configured.")
    if not settings.allowed_chat_ids:
        warnings.append("No Telegram admin/alert chat id is configured.")
    if settings.safe_automation_interval_seconds < 60:
        warnings.append("Automation interval was below 60 and has been forced to 60 seconds.")
    if not settings.provider_priority:
        warnings.append("No market data provider priority is configured.")
    return {
        "exists": parsed["exists"],
        "env_keys_count": len(parsed.get("rows") or []),
        "duplicates": parsed.get("duplicates") or {},
        "groups": group_status,
        "settings_loaded": {
            "database": bool(settings.database_url),
            "telegram_bot_token": bool(settings.telegram_bot_token),
            "telegram_api": bool(settings.telegram_api_id and settings.telegram_api_hash),
            "telegram_admin_chat_ids": len(settings.allowed_chat_ids),
            "automation_enabled": settings.automation_enabled,
            "automation_interval_seconds": settings.safe_automation_interval_seconds,
            "market_data_provider_count": len(settings.provider_priority),
        },
        "warnings": warnings,
    }


def format_env_health(path: str | Path = ".env") -> str:
    health = build_env_health(path)
    lines = ["Environment Health", f".env found: {'yes' if health['exists'] else 'no'}", f"Keys loaded: {health['env_keys_count']}", ""]
    lines.append("Core settings")
    loaded = health["settings_loaded"]
    lines.append(f"- Database URL configured: {'yes' if loaded['database'] else 'no'}")
    lines.append(f"- Telegram bot token configured: {'yes' if loaded['telegram_bot_token'] else 'no'}")
    lines.append(f"- Telegram API configured: {'yes' if loaded['telegram_api'] else 'no'}")
    lines.append(f"- Admin/alert chat ids configured: {loaded['telegram_admin_chat_ids']}")
    lines.append(f"- Automation enabled: {'yes' if loaded['automation_enabled'] else 'no'}")
    lines.append(f"- Automation interval: {loaded['automation_interval_seconds']} seconds")
    lines.append(f"- Market data providers: {loaded['market_data_provider_count']}")
    if health["duplicates"]:
        lines.extend(["", "Duplicate keys"])
        for key, line_numbers in health["duplicates"].items():
            lines.append(f"- {key}: lines {', '.join(str(line) for line in line_numbers)}")
    if health["warnings"]:
        lines.extend(["", "Warnings"])
        lines.extend(f"- {warning}" for warning in health["warnings"])
    else:
        lines.extend(["", "No blocking environment warnings found."])
    lines.append("")
    lines.append("Secret values are hidden by design.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Redacted EGX .env health check.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.json:
        print(json.dumps(build_env_health(), ensure_ascii=True, indent=2, default=str))
    else:
        print(format_env_health())


if __name__ == "__main__":
    main()
