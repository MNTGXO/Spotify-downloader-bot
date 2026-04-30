import os
from dotenv import load_dotenv
from pyrogram import Client, idle

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

app = Client(
    "music_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins")
)

if __name__ == "__main__":
    app.start()
    print("Bot started! 🎶")
    idle()
    app.stop()
