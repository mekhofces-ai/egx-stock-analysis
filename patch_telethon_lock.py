from pathlib import Path
import re

path = Path("app/services/telegram_listener.py")

if not path.exists():
    print("telegram_listener.py not found:", path)
    raise SystemExit

text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak_telethon_lock")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

# Add imports
if "from filelock import FileLock, Timeout" not in text:
    text = "from filelock import FileLock, Timeout\n" + text

if "from pathlib import Path" not in text:
    text = "from pathlib import Path\n" + text

# Add lock constants after imports
lock_block = '''
# Cross-process lock for Telethon session.
# This prevents Dashboard + Automation from using the same .session file at the same time.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TELEGRAM_FETCH_LOCK = _PROJECT_ROOT / "data" / "telegram_fetch.lock"
_TELEGRAM_FETCH_LOCK.parent.mkdir(parents=True, exist_ok=True)

'''

if "_TELEGRAM_FETCH_LOCK" not in text:
    # insert after import lines
    lines = text.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
    lines.insert(insert_at, lock_block)
    text = "\n".join(lines) + "\n"

# Replace fetch_active_channels_once with locked version
func_name = "def fetch_active_channels_once"
start = text.find(func_name)

if start == -1:
    print("fetch_active_channels_once() not found. No function patched.")
    path.write_text(text, encoding="utf-8")
    raise SystemExit

# find next top-level def/class after the function
next_match = re.search(r"\n(?=def\s+|class\s+)", text[start + 1:])
if next_match:
    end = start + 1 + next_match.start()
else:
    end = len(text)

new_func = '''def fetch_active_channels_once():
    """
    Run Telegram ingestion with a cross-process lock.

    Reason:
    Telethon uses a SQLite .session file.
    If Streamlit dashboard, automation_runner, and manual fetch run together,
    Telethon can raise: sqlite3.OperationalError: database is locked.

    This lock makes sure only one process uses the Telegram session at a time.
    """
    lock = FileLock(str(_TELEGRAM_FETCH_LOCK), timeout=2)

    try:
        with lock:
            return asyncio.run(TelegramListener().fetch_once())

    except Timeout:
        print("Telegram fetch skipped: another process is already using the Telegram session.")
        return 0
'''

text = text[:start] + new_func + text[end:]

path.write_text(text, encoding="utf-8")
print("telegram_listener.py patched successfully with Telethon session lock.")
