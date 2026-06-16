from pathlib import Path
import re

bot_path = Path("telegram_bot_service.py")

if not bot_path.exists():
    print("telegram_bot_service.py not found")
    raise SystemExit(1)

text = bot_path.read_text(encoding="utf-8")

backup = bot_path.with_suffix(".py.bak_subscribers")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

# Add sqlite imports if missing
if "import sqlite3" not in text:
    text = text.replace("import time", "import time\nimport sqlite3")
if "from pathlib import Path" not in text:
    text = text.replace("import re", "import re\nfrom pathlib import Path") if "import re" in text else "from pathlib import Path\n" + text
if "from datetime import datetime" not in text:
    text = text.replace("import time", "import time\nfrom datetime import datetime")

subscriber_code = r'''
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
    chat_ids = get_alert_chat_ids()
    sent = 0

    for cid in chat_ids:
        try:
            send_message(cid, text)
            sent += 1
        except Exception as e:
            print("Broadcast failed to", cid, e)

    return sent
# ===== END EGX SUBSCRIBERS PATCH =====

'''

if "EGX SUBSCRIBERS PATCH" not in text:
    # Insert after BASE_URL definition if exists
    m = re.search(r"BASE_URL\s*=\s*f?['\"].*?['\"]", text)
    if m:
        insert_at = m.end()
        text = text[:insert_at] + "\n" + subscriber_code + text[insert_at:]
    else:
        text = subscriber_code + "\n" + text

# Add register_subscriber in handle_message
if "register_subscriber(chat_id, chat_type, user)" not in text:
    text = text.replace(
        "if not chat_id:\n        return",
        "if not chat_id:\n        return\n\n    # Save every user/group that talks to the bot\n    try:\n        register_subscriber(chat_id, chat_type, user)\n    except Exception as e:\n        print('Could not register subscriber:', e)"
    )

# Add commands before /help block
if 'lower == "/subscribe"' not in text:
    marker = 'if lower == "/help":'
    subscribe_block = '''if lower == "/subscribe":
        register_subscriber(chat_id, chat_type, user)
        send_message(chat_id, "✅ تم الاشتراك في تنبيهات EGX.\\nستصلك الفرص والتنبيهات الجديدة هنا.")
        return

    if lower == "/unsubscribe":
        unregister_subscriber(chat_id)
        send_message(chat_id, "تم إلغاء الاشتراك من التنبيهات.")
        return

    if lower == "/subscribers":
        subs = get_subscribers()
        send_message(chat_id, f"👥 Active subscribers: {len(subs)}")
        return

    '''
    if marker in text:
        text = text.replace(marker, subscribe_block + marker)

# Replace alert sending to private single chat where possible
# Common pattern: send_message(target_chat_id, text)
text = text.replace("send_message(target_chat_id, text)", "broadcast_message(text)")
text = text.replace("send_message(PRIVATE_CHAT_ID, text)", "broadcast_message(text)")
text = text.replace("send_message(private_chat_id, text)", "broadcast_message(text)")

# Make /alerts command report broadcast
text = text.replace(
    'send_message(chat_id, f"✅ Alerts sent: {sent}")',
    'send_message(chat_id, f"✅ Alerts broadcasted to subscribers. Sent count: {sent}")'
)

bot_path.write_text(text, encoding="utf-8")
print("telegram_bot_service.py patched with subscribers support.")
