from pathlib import Path
import requests
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

env_path = Path(".env")

print("Telegram Bot Setup")
print("==================")
print("IMPORTANT:")
print("1) Go to BotFather")
print("2) Send /revoke")
print("3) Choose your bot")
print("4) Copy the NEW token")
print()
print("Do NOT use the old token.")
print()

token = input("Paste NEW TELEGRAM_BOT_TOKEN here: ").strip()

if not token or ":" not in token:
    print("Invalid token format. It must look like: 1234567890:AAxxxx")
    input("Press Enter to close...")
    raise SystemExit

base_url = f"https://api.telegram.org/bot{token}"

def tg_get(method, **kwargs):
    try:
        return requests.get(base_url + "/" + method, timeout=20, **kwargs)
    except requests.exceptions.SSLError:
        print("SSL certificate problem detected. Retrying without SSL verification...")
        return requests.get(base_url + "/" + method, timeout=20, verify=False, **kwargs)

def tg_post(method, **kwargs):
    try:
        return requests.post(base_url + "/" + method, timeout=20, **kwargs)
    except requests.exceptions.SSLError:
        print("SSL certificate problem detected. Retrying without SSL verification...")
        return requests.post(base_url + "/" + method, timeout=20, verify=False, **kwargs)

print()
print("Checking token...")

try:
    r = tg_get("getMe")
    data = r.json()
except Exception as e:
    print("Connection error:")
    print(e)
    input("Press Enter to close...")
    raise SystemExit

if not data.get("ok"):
    print()
    print("Token is not valid.")
    print(data)
    print()
    print("This means the token is wrong or revoked.")
    print("Go to BotFather > /revoke > get a NEW token > run this script again.")
    input("Press Enter to close...")
    raise SystemExit

bot_username = data["result"]["username"]
print(f"Token is valid. Bot: @{bot_username}")

# Read .env
lines = []
if env_path.exists():
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

set_env("TELEGRAM_BOT_TOKEN", token)
set_env("BOT_TOKEN", token)
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print()
print("Token saved to .env.")
print()
print("Now open Telegram and send this message to your bot:")
print("/start")
print()
input("After sending /start to the bot, press Enter here...")

try:
    tg_post("deleteWebhook", data={"drop_pending_updates": False})
except Exception:
    pass

print()
print("Searching for private chat id...")

chat_id = None

for i in range(40):
    try:
        updates = tg_get("getUpdates").json()

        for update in updates.get("result", []):
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue

            chat = msg.get("chat", {})

            if chat.get("type") == "private":
                chat_id = chat.get("id")
                first_name = chat.get("first_name", "")
                username = chat.get("username", "")

                print()
                print("PRIVATE CHAT ID FOUND:")
                print(chat_id)
                print("Name:", first_name)
                print("Username:", username)
                break

        if chat_id:
            break

        print("Waiting... make sure you sent /start to the bot.")
        time.sleep(2)

    except Exception as e:
        print("Error:", e)
        time.sleep(2)

if not chat_id:
    print()
    print("No private chat id found.")
    print("Open the bot in Telegram, send /start, then run this script again.")
    input("Press Enter to close...")
    raise SystemExit

set_env("TELEGRAM_PRIVATE_CHAT_ID", str(chat_id))
set_env("PRIVATE_CHAT_ID", str(chat_id))
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print()
print(".env updated successfully.")
print("Added:")
print(f"TELEGRAM_PRIVATE_CHAT_ID={chat_id}")
print(f"PRIVATE_CHAT_ID={chat_id}")
print()
print("Now run:")
print("streamlit run dashboard/streamlit_app.py --server.port 8509")

input("Press Enter to close...")
