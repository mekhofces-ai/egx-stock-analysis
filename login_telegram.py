from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from dotenv import load_dotenv
import os

load_dotenv()

print("===================================")
print(" Telegram User Login")
print("===================================")
print("This will NOT open Telegram.")
print("It will send a code to your Telegram app or SMS.")
print("Then you must type the code here in PowerShell.")
print()

api_id_env = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID")
api_hash_env = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")

if api_id_env:
    api_id = int(api_id_env)
    print("Using API ID from .env")
else:
    api_id = int(input("Enter Telegram API ID: ").strip())

if api_hash_env:
    api_hash = api_hash_env
    print("Using API HASH from .env")
else:
    api_hash = input("Enter Telegram API HASH: ").strip()

session_name = input("Enter session name or press Enter for egx_session: ").strip()
if not session_name:
    session_name = "egx_session"

phone = input("Enter your mobile number with country code, example +201xxxxxxxxx: ").strip()

session_file = session_name + ".session"

if os.path.exists(session_file):
    delete_old = input(f"Old session file found ({session_file}). Delete it and login again? y/n: ").strip().lower()
    if delete_old == "y":
        os.remove(session_file)
        print("Old session deleted.")

client = TelegramClient(session_name, api_id, api_hash)
client.connect()

try:
    if not client.is_user_authorized():
        print()
        print("Sending login code now...")
        sent = client.send_code_request(phone)

        code = input("Enter the Telegram code you received: ").strip()

        try:
            client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)

        except SessionPasswordNeededError:
            password = input("Your Telegram account has 2FA password. Enter password: ").strip()
            client.sign_in(password=password)

        except PhoneCodeInvalidError:
            print("Invalid code. Run the script again and enter the correct code.")
            client.disconnect()
            exit()

        except PhoneCodeExpiredError:
            print("Code expired. Run the script again to get a new code.")
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
    print("Session file:", session_file)

finally:
    client.disconnect()
