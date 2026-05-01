import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server started on port %s", port)
    return server


app = Client(
    "music_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="plugins"),
)


if __name__ == "__main__":
    health_server = start_health_server()
    app.start()
    logger.info("Bot started")

    try:
        me = app.get_me()
        app.send_message(
            OWNER_NOTIFY_ID,
            f"✅ {me.first_name} restarted successfully and is now online.",
        )
    except Exception as exc:
        logger.warning("Startup notification failed: %s", exc)

    idle()
    app.stop()
    health_server.shutdown()
