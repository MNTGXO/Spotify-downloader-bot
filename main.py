import asyncio
import logging
import os

from aiohttp import web
from dotenv import load_dotenv
from pyrogram import Client, idle

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_NOTIFY_ID = int(os.environ.get("OWNER_NOTIFY_ID", "1892771262"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("music_downloader_bot")

app = Client(
    "music_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins"),
)


async def health_check(request):
    return web.Response(text="Bot is alive!")


async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health check server started on port %s", port)


async def startup_notify():
    try:
        me = await app.get_me()
        await app.send_message(
            OWNER_NOTIFY_ID,
            f"✅ {me.first_name} restarted successfully and is now online.",
        )
    except Exception as exc:
        logger.warning("Startup notification failed: %s", exc)


async def main():
    await app.start()
    logger.info("Bot started")
    await start_web_server()
    await startup_notify()
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
