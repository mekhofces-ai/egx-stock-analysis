from pathlib import Path

path = Path("telegram_bot_service.py")

if not path.exists():
    print("telegram_bot_service.py not found")
    raise SystemExit(1)

text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak_fix_future_import")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

lines = text.splitlines()

# Remove all existing misplaced future imports
clean_lines = []
for line in lines:
    if line.strip() == "from __future__ import annotations":
        continue
    clean_lines.append(line)

# Find correct insertion point: after shebang/coding/blank lines only
insert_at = 0
while insert_at < len(clean_lines):
    s = clean_lines[insert_at].strip()
    if s.startswith("#!") or "coding" in s.lower() or s == "":
        insert_at += 1
    else:
        break

# Put future import at the correct location
clean_lines.insert(insert_at, "from __future__ import annotations")

path.write_text("\n".join(clean_lines) + "\n", encoding="utf-8")

print("telegram_bot_service.py fixed.")
