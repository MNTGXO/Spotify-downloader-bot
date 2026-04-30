from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@Client.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        f"**🎵 Welcome {message.from_user.mention}!**\n\n"
        "Send me a music link from **Spotify**, **YouTube**, **YT Music**, **SoundCloud**, **Deezer** and I'll download it in high quality.\n\n"
        "**Commands:**\n"
        "/start - Show this\n/help - Detailed help\n/about - About me",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help", callback_data="help"),
             InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ])
    )
