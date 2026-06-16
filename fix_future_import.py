from pathlib import Path
import re

path = Path("app/services/telegram_listener.py")

if not path.exists():
    print("telegram_listener.py not found")
    raise SystemExit(1)

text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak_fix_future_import")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

# Remove duplicated / misplaced imports
lines = text.splitlines()

clean_lines = []
for line in lines:
    stripped = line.strip()

    if stripped == "from __future__ import annotations":
        continue

    if stripped == "from filelock import FileLock, Timeout":
        continue

    if stripped == "from pathlib import Path":
        continue

    clean_lines.append(line)

# Keep encoding/shebang comments first
insert_at = 0
while insert_at < len(clean_lines):
    s = clean_lines[insert_at].strip()

    if s.startswith("#!") or "coding" in s.lower():
        insert_at += 1
    elif s == "":
        insert_at += 1
    else:
        break

# Insert imports in correct order
fixed_imports = [
    "from __future__ import annotations",
    "from pathlib import Path",
    "from filelock import FileLock, Timeout",
    "",
]

clean_lines[insert_at:insert_at] = fixed_imports

text = "\n".join(clean_lines) + "\n"

# Make sure lock block exists
lock_block = '''
# Cross-process lock for Telethon session.
# This prevents Dashboard + Automation from using the same .session file at the same time.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TELEGRAM_FETCH_LOCK = _PROJECT_ROOT / "data" / "telegram_fetch.lock"
_TELEGRAM_FETCH_LOCK.parent.mkdir(parents=True, exist_ok=True)

'''

if "_TELEGRAM_FETCH_LOCK" not in text:
    # Insert after imports
    lines = text.splitlines()
    insert_after_imports = 0

    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_after_imports = i + 1

    lines.insert(insert_after_imports, lock_block)
    text = "\n".join(lines) + "\n"

path.write_text(text, encoding="utf-8")

print("telegram_listener.py import order fixed successfully.")
