
import os

import requests
from dotenv import load_dotenv


load_dotenv()

token = os.environ["TELEGRAM_BOT_TOKEN"]

response = requests.get(
    f"https://api.telegram.org/bot{token}/getUpdates",
    timeout=15,
)
response.raise_for_status()

for update in response.json()["result"]:
    message = update.get("message", {})
    chat = message.get("chat", {})

    if chat:
        print("Chat ID:", chat["id"])
        print("Chat name:", chat.get("title") or chat.get("first_name"))
