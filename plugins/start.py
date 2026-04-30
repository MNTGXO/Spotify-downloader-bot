from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@Client.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        f"**🎵 Welcome {message.from_user.mention}!**\n\n"
        "Send me a music link from **anywhere**:\n"
        "• Spotify\n• YouTube / YouTube Music\n• SoundCloud\n• Deezer\n• Apple Music\n"
        "and many more.\n\n"
        "I'll download it in high‑quality **320kbps MP3** with full metadata & cover art.\n\n"
        "**Commands:**\n"
        "/start - Show this\n/help - Detailed help\n/about - About me",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help", callback_data="help"),
             InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ])
    )
