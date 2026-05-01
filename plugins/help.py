from pyrogram import Client, filters

@Client.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "**🎶 How to use**\n\n"
        "1. Send me a track/album/playlist link.\n"
        "2. Choose output: **MP3**, **M4A**, or **Best available**.\n"
        "3. I'll process and upload the audio file.\n\n"
        "**Supported platforms:** Spotify, YouTube (video + music), "
        "SoundCloud, Deezer, Apple Music and more.",
        disable_web_page_preview=True
    )
