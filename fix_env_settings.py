from pathlib import Path
from dotenv import dotenv_values

env_path = Path(".env")

if not env_path.exists():
    print(".env not found")
    raise SystemExit

# Read and remove BOM if exists
raw = env_path.read_text(encoding="utf-8-sig")
lines = raw.splitlines()

values = dotenv_values(env_path)

def get_any(*keys):
    for k in keys:
        v = values.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""

token = get_any("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
chat_id = get_any(
    "TELEGRAM_BOT_PRIVATE_CHAT_ID",
    "TELEGRAM_PRIVATE_CHAT_ID",
    "PRIVATE_CHAT_ID",
    "CHAT_ID"
)

session_name = get_any("TELEGRAM_SESSION_NAME", "TELEGRAM_SESSION", "SESSION_NAME")
if not session_name:
    session_name = "egx_telegram"

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
            # remove BOM from any existing line
            new_lines.append(line.lstrip("\ufeff"))

    if not found:
        new_lines.append(f"{key}={value}")

    lines = new_lines

# Keep existing token/chat id, just normalize aliases
if token:
    set_env("TELEGRAM_BOT_TOKEN", token)
    set_env("BOT_TOKEN", token)
else:
    print("WARNING: TELEGRAM_BOT_TOKEN not found")

if chat_id:
    set_env("TELEGRAM_BOT_PRIVATE_CHAT_ID", chat_id)
    set_env("TELEGRAM_PRIVATE_CHAT_ID", chat_id)
    set_env("PRIVATE_CHAT_ID", chat_id)
else:
    print("WARNING: TELEGRAM_BOT_PRIVATE_CHAT_ID not found")

# Telegram user session aliases
set_env("TELEGRAM_SESSION_NAME", session_name)
set_env("TELEGRAM_SESSION", session_name)
set_env("SESSION_NAME", session_name)

# Reduce Telegram load
set_env("TELEGRAM_FETCH_LIMIT", "50")
set_env("TELEGRAM_FETCH_LIMIT_PER_CHANNEL", "50")

# Automation keys used by newer runner
set_env("AUTOMATION_ENABLED", "true")
set_env("AUTOMATION_INTERVAL_SECONDS", "120")

# Existing scheduler style keys
set_env("SCHEDULER_ENABLED", "true")
set_env("TELEGRAM_FETCH_INTERVAL_MINUTES", "2")
set_env("ANALYSIS_INTERVAL_MINUTES", "2")
set_env("PERFORMANCE_INTERVAL_MINUTES", "5")

# Streamlit/API refresh
set_env("AUTO_REFRESH_ENABLED", "true")
set_env("AUTO_REFRESH_ON_START", "false")
set_env("AUTO_REFRESH_INTERVAL_MS", "120000")

# SQLite safety
set_env("SQLITE_TIMEOUT", "30")
set_env("SQLITE_BUSY_TIMEOUT", "30000")

# Telegram alerts
set_env("TELEGRAM_ALERT_ENABLED", "true")
set_env("TELEGRAM_ALERT_DECISIONS", "BUY,STRONG BUY,WEAK BUY")
set_env("TELEGRAM_ALERT_MIN_CONFIDENCE", "60")
set_env("TELEGRAM_ALERT_RECOMMENDATIONS_ENABLED", "true")

# Important: true may block alerts if confirmation signal is missing
set_env("TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION", "false")
set_env("TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES", "2")

# Media handling flags for code/Codex to use
set_env("TELEGRAM_DOWNLOAD_MEDIA", "false")
set_env("TELEGRAM_ANALYZE_IMAGES", "false")
set_env("TELEGRAM_SKIP_NON_IMAGE_MEDIA", "true")
set_env("TELEGRAM_ALLOWED_IMAGE_EXTENSIONS", ".jpg,.jpeg,.png,.webp,.bmp")

# Keep app env
set_env("ENV", "local")
set_env("APP_ENV", "development")
set_env("TIMEZONE", "Africa/Cairo")

# Write UTF-8 without BOM
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env fixed successfully.")
print("Token exists:", "YES" if token else "NO")
print("Private chat id exists:", "YES" if chat_id else "NO")
print("Session name:", session_name)
print("Automation interval: 120 seconds")
print("Telegram fetch limit per channel: 50")
print("Auto refresh on start: false")
print("Telegram confirmation required: false")
