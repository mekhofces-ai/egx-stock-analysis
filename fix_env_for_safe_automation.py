from pathlib import Path
from dotenv import dotenv_values

env_path = Path(".env")

if not env_path.exists():
    env_path.write_text("", encoding="utf-8")

raw = env_path.read_text(encoding="utf-8-sig")
lines = raw.splitlines()
values = dotenv_values(env_path)

def set_env(key, value):
    global lines
    found = False
    new_lines = []

    for line in lines:
        clean = line.strip().lstrip("\ufeff")
        if clean.startswith(key + "="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line.lstrip("\ufeff"))

    if not found:
        new_lines.append(f"{key}={value}")

    lines = new_lines

# Safe automation
set_env("AUTOMATION_ENABLED", "true")
set_env("AUTOMATION_INTERVAL_SECONDS", "120")
set_env("BACKTEST_INTERVAL_SECONDS", "3600")

# Telegram safe fetch
set_env("TELEGRAM_FETCH_LIMIT", "30")
set_env("TELEGRAM_FETCH_LIMIT_PER_CHANNEL", "30")
set_env("TELEGRAM_DOWNLOAD_MEDIA", "false")
set_env("TELEGRAM_ANALYZE_IMAGES", "false")
set_env("TELEGRAM_SKIP_NON_IMAGE_MEDIA", "true")
set_env("TELEGRAM_ALLOWED_IMAGE_EXTENSIONS", ".jpg,.jpeg,.png,.webp,.bmp")

# Avoid dashboard auto fetch conflict
set_env("AUTO_REFRESH_ON_START", "false")
set_env("AUTO_REFRESH_INTERVAL_MS", "120000")

# Alerts
set_env("TELEGRAM_ALERT_ENABLED", "true")
set_env("TELEGRAM_ALERT_DECISIONS", "BUY,STRONG BUY,WEAK BUY")
set_env("TELEGRAM_ALERT_MIN_CONFIDENCE", "60")
set_env("TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION", "false")
set_env("TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES", "2")

# SQLite safety
set_env("SQLITE_TIMEOUT", "30")
set_env("SQLITE_BUSY_TIMEOUT", "30000")

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env updated safely.")
