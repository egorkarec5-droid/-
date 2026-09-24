import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
BOT_NAME = "Досье Плюс"

if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не задана")
