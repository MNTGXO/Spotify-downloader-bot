from pyrogram import Client, filters

@Client.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "**🎶 How to use**\n\n"
        "1. Send me a track/album/playlist link.\n"
        "2. I'll show you the track details & cover.\n"
        "3. Choose **320kbps MP3** or **128kbps MP3**.\n"
        "4. I'll upload the song for you.\n\n"
        "**Supported platforms:** Spotify, YouTube (video + music), "
        "SoundCloud, Deezer, Apple Music and more.\n\n"
        "For albums/playlists, you'll get an inline list – tap any track to download it.",
        disable_web_page_preview=True
    )
