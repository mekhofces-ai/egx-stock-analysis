from pathlib import Path

path = Path("telegram_bot_service.py")

if not path.exists():
    print("telegram_bot_service.py not found")
    raise SystemExit(1)

text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak_fix_imports")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

lines = text.splitlines()

# Remove duplicated critical imports from anywhere
remove_exact = {
    "from __future__ import annotations",
    "from pathlib import Path",
    "import sqlite3",
    "from datetime import datetime",
}

clean = []
for line in lines:
    if line.strip() in remove_exact:
        continue
    clean.append(line)

# Keep shebang/coding/empty lines first
insert_at = 0
while insert_at < len(clean):
    s = clean[insert_at].strip()
    if s.startswith("#!") or "coding" in s.lower() or s == "":
        insert_at += 1
    else:
        break

# Required imports must be before any code using Path/sqlite3/datetime
required_top = [
    "from __future__ import annotations",
    "from pathlib import Path",
    "from datetime import datetime",
    "import sqlite3",
    "",
]

clean[insert_at:insert_at] = required_top

path.write_text("\n".join(clean) + "\n", encoding="utf-8")

print("telegram_bot_service.py imports fixed.")
