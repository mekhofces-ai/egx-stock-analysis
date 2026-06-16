from pathlib import Path
from dotenv import dotenv_values
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

env_path = Path(".env")

if not env_path.exists():
    print(".env not found")
    raise SystemExit

raw = env_path.read_text(encoding="utf-8-sig")
lines = raw.splitlines()
values = dotenv_values(env_path)

def get_value(*keys):
    for key in keys:
        value = values.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""

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

token = get_value("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")

if not token:
    print("No bot token found.")
    raise SystemExit

# Main admin chat only
main_chat_id = "8431194056"

# All users/groups who should receive alerts
allowed_chat_ids = [
    "8431194056",
    "132600103"
]

allowed_chat_ids = list(dict.fromkeys([x.strip() for x in allowed_chat_ids if x.strip()]))

set_env("TELEGRAM_BOT_TOKEN", token)
set_env("BOT_TOKEN", token)

set_env("TELEGRAM_BOT_PRIVATE_CHAT_ID", main_chat_id)
set_env("TELEGRAM_PRIVATE_CHAT_ID", main_chat_id)
set_env("PRIVATE_CHAT_ID", main_chat_id)

set_env("TELEGRAM_BOT_ALLOWED_CHAT_IDS", ",".join(allowed_chat_ids))

set_env("TELEGRAM_ALERT_ENABLED", "true")
set_env("TELEGRAM_ALERT_DECISIONS", "BUY,STRONG BUY,WEAK BUY")
set_env("TELEGRAM_ALERT_MIN_CONFIDENCE", "60")
set_env("TELEGRAM_ALERT_RECOMMENDATIONS_ENABLED", "true")
set_env("TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION", "false")

set_env("AUTOMATION_ENABLED", "true")
set_env("AUTOMATION_INTERVAL_SECONDS", "120")
set_env("AUTOMATION_RUN_BACKTEST", "false")

set_env("TELEGRAM_DOWNLOAD_MEDIA", "false")
set_env("TELEGRAM_ANALYZE_IMAGES", "false")
set_env("TELEGRAM_SKIP_NON_IMAGE_MEDIA", "true")

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env fixed successfully.")
print("Main private chat id:", main_chat_id)
print("Allowed alert chat ids:", ",".join(allowed_chat_ids))

base = f"https://api.telegram.org/bot{token}"

def post(method, **kwargs):
    try:
        return requests.post(base + "/" + method, timeout=25, **kwargs)
    except requests.exceptions.SSLError:
        return requests.post(base + "/" + method, timeout=25, verify=False, **kwargs)

print()
print("Sending test message to all allowed chat IDs...")

sent = 0
failed = 0

for chat_id in allowed_chat_ids:
    res = post(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": "✅ EGX Bot test message\n\nلو الرسالة وصلتك، التنبيهات هتوصلك من السيستم."
        }
    )

    try:
        data = res.json()
    except Exception:
        data = {"ok": False, "description": res.text}

    if data.get("ok"):
        print("✅ Sent to:", chat_id)
        sent += 1
    else:
        print("❌ Failed to:", chat_id)
        print(data)
        failed += 1

print()
print("Finished.")
print("Sent:", sent)
print("Failed:", failed)

if failed:
    print()
    print("Important:")
    print("Any failed user must open the bot and send /start first.")
