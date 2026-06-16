from pathlib import Path
from dotenv import dotenv_values

env_path = Path(".env")

if not env_path.exists():
    print(".env not found")
    raise SystemExit

values = dotenv_values(env_path)

token = (
    values.get("TELEGRAM_BOT_TOKEN")
    or values.get("BOT_TOKEN")
)

chat_id = (
    values.get("TELEGRAM_BOT_PRIVATE_CHAT_ID")
    or values.get("TELEGRAM_PRIVATE_CHAT_ID")
    or values.get("PRIVATE_CHAT_ID")
    or values.get("TELEGRAM_CHAT_ID")
    or values.get("CHAT_ID")
)

if not token:
    print("TELEGRAM_BOT_TOKEN not found in .env")
    raise SystemExit

if not chat_id:
    print("Chat ID not found in .env")
    print("Expected one of:")
    print("TELEGRAM_BOT_PRIVATE_CHAT_ID / TELEGRAM_PRIVATE_CHAT_ID / PRIVATE_CHAT_ID")
    raise SystemExit

lines = env_path.read_text(encoding="utf-8").splitlines()

def set_env(key, value):
    global lines
    found = False
    new_lines = []

    for line in lines:
        if line.strip().startswith(key + "="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}")

    lines = new_lines

# Required by your app warning
set_env("TELEGRAM_BOT_TOKEN", token)
set_env("TELEGRAM_BOT_PRIVATE_CHAT_ID", chat_id)

# Keep aliases too, for other code files
set_env("BOT_TOKEN", token)
set_env("TELEGRAM_PRIVATE_CHAT_ID", chat_id)
set_env("PRIVATE_CHAT_ID", chat_id)

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(".env fixed successfully.")
print("Added/updated:")
print("TELEGRAM_BOT_TOKEN=********")
print(f"TELEGRAM_BOT_PRIVATE_CHAT_ID={chat_id}")
print("TELEGRAM_PRIVATE_CHAT_ID also updated.")
print("PRIVATE_CHAT_ID also updated.")
