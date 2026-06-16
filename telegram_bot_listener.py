from dotenv import load_dotenv
import os
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

if not TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN not found in .env")
    print("Add it like this:")
    print("TELEGRAM_BOT_TOKEN=your_token_here")
    raise SystemExit

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"


def tg_get(method, **kwargs):
    try:
        return requests.get(BASE_URL + "/" + method, timeout=40, **kwargs)
    except requests.exceptions.SSLError:
        return requests.get(BASE_URL + "/" + method, timeout=40, verify=False, **kwargs)


def tg_post(method, **kwargs):
    try:
        return requests.post(BASE_URL + "/" + method, timeout=20, **kwargs)
    except requests.exceptions.SSLError:
        return requests.post(BASE_URL + "/" + method, timeout=20, verify=False, **kwargs)


def send_message(chat_id, text):
    tg_post(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
    )


def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    chat_type = chat.get("type")
    text = (message.get("text") or "").strip()

    first_name = user.get("first_name") or ""
    username = user.get("username") or ""

    print("--------------------------------")
    print("Chat ID:", chat_id)
    print("Chat Type:", chat_type)
    print("User:", first_name, username)
    print("Message:", text)

    if not chat_id:
        return

    if text == "/start":
        reply = (
            "أهلاً بيك في بوت EGX ✅\n\n"
            "البوت شغال دلوقتي.\n"
            "اكتب /help علشان تشوف الأوامر المتاحة."
        )

    elif text == "/help":
        reply = (
            "📌 الأوامر المتاحة:\n\n"
            "/start - تشغيل البوت\n"
            "/help - عرض الأوامر\n"
            "/status - حالة البوت\n"
            "/id - عرض Chat ID الخاص بالمحادثة\n\n"
            "تقدر تبعت أي رسالة، والبوت هيرد عليك."
        )

    elif text == "/status":
        reply = "✅ Bot status: Running"

    elif text == "/id":
        reply = (
            "🆔 Chat ID:\n"
            f"<code>{chat_id}</code>\n\n"
            f"Chat type: {chat_type}"
        )

    else:
        reply = (
            "✅ وصلت رسالتك.\n\n"
            "الرسالة:\n"
            f"{text}"
        )

    send_message(chat_id, reply)


def main():
    print("Checking bot token...")

    me = tg_get("getMe").json()

    if not me.get("ok"):
        print("❌ Bot token invalid:")
        print(me)
        raise SystemExit

    bot_username = me["result"]["username"]

    print(f"✅ Bot is running: @{bot_username}")

    # Important for polling
    tg_post("deleteWebhook", data={"drop_pending_updates": False})

    print()
    print("Bot listener started ✅")
    print("Open Telegram and send /start to your bot.")
    print("Keep this PowerShell window open.")
    print("Press CTRL + C to stop.")
    print()

    offset = None

    while True:
        try:
            params = {"timeout": 30}

            if offset is not None:
                params["offset"] = offset

            updates = tg_get("getUpdates", params=params).json()

            if not updates.get("ok"):
                print("getUpdates error:", updates)
                time.sleep(3)
                continue

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                message = (
                    update.get("message")
                    or update.get("edited_message")
                )

                if message:
                    handle_message(message)

        except KeyboardInterrupt:
            print()
            print("Bot stopped.")
            break

        except Exception as e:
            print("Error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
