from pathlib import Path
from dotenv import dotenv_values
import requests
import urllib3
import sqlite3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT = Path(".")
ENV_PATH = PROJECT / ".env"
BOT_PATH = PROJECT / "telegram_bot_service.py"
SUB_DB = PROJECT / "data" / "telegram_subscribers.db"

if not ENV_PATH.exists():
    print(".env not found")
    raise SystemExit(1)

raw = ENV_PATH.read_text(encoding="utf-8-sig")
lines = raw.splitlines()
values = dotenv_values(ENV_PATH)

def get_value(*keys):
    for key in keys:
        value = values.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""

def split_ids(value):
    if not value:
        return []
    return [x.strip() for x in str(value).replace(";", ",").split(",") if x.strip()]

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
    print("No bot token found in .env")
    raise SystemExit(1)

# Main admin/private chat must be ONE id only
main_private = get_value("TELEGRAM_BOT_PRIVATE_CHAT_ID", "TELEGRAM_PRIVATE_CHAT_ID", "PRIVATE_CHAT_ID")
main_private_first = split_ids(main_private)[0] if split_ids(main_private) else ""

# Your known users
ids = []
ids += split_ids(get_value("TELEGRAM_BOT_ALLOWED_CHAT_IDS"))
ids += split_ids(get_value("PRIVATE_CHAT_ID"))
ids += split_ids(get_value("TELEGRAM_PRIVATE_CHAT_ID"))
ids += split_ids(get_value("TELEGRAM_BOT_PRIVATE_CHAT_ID"))

# Add the two IDs you currently want
ids += ["8431194056", "132600103"]

# Unique
allowed = []
seen = set()
for cid in ids:
    if cid and cid not in seen:
        allowed.append(cid)
        seen.add(cid)

if not main_private_first:
    main_private_first = allowed[0] if allowed else ""

if not allowed:
    print("No chat IDs found.")
    raise SystemExit(1)

# Fix env
set_env("TELEGRAM_BOT_TOKEN", token)
set_env("BOT_TOKEN", token)

set_env("TELEGRAM_BOT_PRIVATE_CHAT_ID", main_private_first)
set_env("TELEGRAM_PRIVATE_CHAT_ID", main_private_first)
set_env("PRIVATE_CHAT_ID", main_private_first)

set_env("TELEGRAM_BOT_ALLOWED_CHAT_IDS", ",".join(allowed))

ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env fixed.")
print("Main private chat id:", main_private_first)
print("Allowed chat ids:", ",".join(allowed))

# Save allowed ids to subscribers database too
SUB_DB.parent.mkdir(parents=True, exist_ok=True)

with sqlite3.connect(str(SUB_DB)) as conn:
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

    for cid in allowed:
        conn.execute("""
            INSERT INTO telegram_subscribers
            (chat_id, chat_type, username, first_name, last_name, is_active, subscribed_at, updated_at)
            VALUES (?, 'private', '', '', '', 1, datetime('now'), datetime('now'))
            ON CONFLICT(chat_id) DO UPDATE SET
                is_active=1,
                updated_at=datetime('now')
        """, (cid,))

    conn.commit()

print("Subscribers database updated.")

# Telegram API helpers
base = f"https://api.telegram.org/bot{token}"

def post(method, **kwargs):
    try:
        return requests.post(base + "/" + method, timeout=25, **kwargs)
    except requests.exceptions.SSLError:
        return requests.post(base + "/" + method, timeout=25, verify=False, **kwargs)

def get(method, **kwargs):
    try:
        return requests.get(base + "/" + method, timeout=25, **kwargs)
    except requests.exceptions.SSLError:
        return requests.get(base + "/" + method, timeout=25, verify=False, **kwargs)

print()
print("Checking bot token...")
me = get("getMe").json()

if not me.get("ok"):
    print("Bot token invalid:")
    print(me)
    raise SystemExit(1)

print("Bot OK:", "@" + me["result"]["username"])

post("deleteWebhook", data={"drop_pending_updates": False})

print()
print("Sending direct test to all allowed chat IDs...")

sent = 0
failed = 0

for cid in allowed:
    res = post(
        "sendMessage",
        data={
            "chat_id": cid,
            "text": "✅ EGX Bot direct test\n\nلو الرسالة وصلتك، يبقى Chat ID متسجل والتنبيهات المفروض توصلك."
        }
    )

    try:
        data = res.json()
    except Exception:
        data = {"ok": False, "description": res.text}

    if data.get("ok"):
        print("✅ Sent to:", cid)
        sent += 1
    else:
        print("❌ Failed to:", cid)
        print(data)
        failed += 1

        desc = str(data.get("description", "")).lower()
        if "chat not found" in desc:
            print("Reason: غالبًا اليوزر ده ماعملش /start للبوت أو Chat ID غلط.")
        if "bot was blocked" in desc or "forbidden" in desc:
            print("Reason: اليوزر عامل block للبوت أو مابدأش محادثة معاه.")

print()
print("Direct test result:")
print("Sent:", sent)
print("Failed:", failed)

# Patch telegram_bot_service.py to broadcast alerts to all allowed ids
if BOT_PATH.exists():
    bot_text = BOT_PATH.read_text(encoding="utf-8")

    backup = BOT_PATH.with_suffix(".py.bak_broadcast_fix")
    backup.write_text(bot_text, encoding="utf-8")
    print("Bot backup created:", backup)

    patch = r'''
# ===== EGX BROADCAST ALERT OVERRIDE =====
def _egx_split_chat_ids(value):
    if not value:
        return []
    return [x.strip() for x in str(value).replace(";", ",").split(",") if x.strip()]

def _egx_get_all_alert_chat_ids():
    ids = []
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_BOT_PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("PRIVATE_CHAT_ID"))
    ids += _egx_split_chat_ids(os.getenv("TELEGRAM_BOT_ALLOWED_CHAT_IDS"))

    try:
        import sqlite3
        from pathlib import Path
        db = Path(__file__).resolve().parent / "data" / "telegram_subscribers.db"
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute(
                    "SELECT chat_id FROM telegram_subscribers WHERE is_active=1"
                ).fetchall()
                ids += [str(r[0]) for r in rows]
    except Exception as e:
        print("Could not read subscribers for broadcast:", e)

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

'''

    # Remove old override if exists
    bot_text = re.sub(
        r"\n?# ===== EGX BROADCAST ALERT OVERRIDE =====.*?# ===== END EGX BROADCAST ALERT OVERRIDE =====\n?",
        "\n",
        bot_text,
        flags=re.S
    )

    # Insert before main() call
    marker = 'if __name__ == "__main__":'
    if marker in bot_text:
        bot_text = bot_text.replace(marker, patch + "\n" + marker)
    else:
        bot_text += "\n" + patch

    BOT_PATH.write_text(bot_text, encoding="utf-8")
    print("telegram_bot_service.py patched to broadcast alerts.")
else:
    print("telegram_bot_service.py not found, skipped patch.")

print()
print("Done.")
print("Next:")
print("1) Ask your friend to send /start to the bot.")
print("2) Run: .\\run_bot.ps1")
print("3) Send /alerts in Telegram.")
