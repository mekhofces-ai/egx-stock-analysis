from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from dotenv import load_dotenv
import os

load_dotenv()

print("===================================")
print(" Telegram Fresh Login")
print("===================================")
print("This will send a login code to your Telegram app or SMS.")
print("Do NOT use bot token here.")
print()

api_id_env = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID")
api_hash_env = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")

if api_id_env:
    api_id = int(api_id_env)
    print("Using API ID from .env")
else:
    api_id = int(input("Enter Telegram API ID from my.telegram.org: ").strip())

if api_hash_env:
    api_hash = api_hash_env
    print("Using API HASH from .env")
else:
    api_hash = input("Enter Telegram API HASH from my.telegram.org: ").strip()

session_name = os.getenv("TELEGRAM_SESSION_NAME") or "egx_streamlit_session"

phone = input("Enter your mobile number with country code, example +201xxxxxxxxx: ").strip()

client = TelegramClient(session_name, api_id, api_hash)
client.connect()

try:
    if not client.is_user_authorized():
        print()
        print("Sending Telegram login code...")
        sent = client.send_code_request(phone)

        code = input("Enter Telegram code here: ").strip()

        try:
            client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)

        except SessionPasswordNeededError:
            password = input("Enter Telegram 2FA password: ").strip()
            client.sign_in(password=password)

        except PhoneCodeInvalidError:
            print("Invalid code. Run again.")
            client.disconnect()
            exit()

        except PhoneCodeExpiredError:
            print("Code expired. Run again.")
            client.disconnect()
            exit()

    me = client.get_me()

    print()
    print("===================================")
    print(" Login successful")
    print("===================================")
    print("Name:", me.first_name)
    print("Username:", me.username)
    print("Phone:", me.phone)
    print("Session file:", session_name + ".session")

finally:
    client.disconnect()
