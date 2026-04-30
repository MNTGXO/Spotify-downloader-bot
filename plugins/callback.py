import os, asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from plugins.download import download_requests
from plugins.spotify_client import spotdl

@Client.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    data = callback_query.data

    # --- Generic help/about buttons (from start.py) ---
    if data == "help":
        await callback_query.answer()
        await callback_query.message.edit_text("Use /help for full instructions.")
        return
    if data == "about":
        await callback_query.answer()
        await callback_query.message.edit_text("🎵 Advanced Music Downloader Bot\nPowered by Pyrogram & spotdl.")
        return

    # --- Cancel button ---
    if data.startswith("cancel_"):
        req_id = data.split("_", 1)[1]
        download_requests.pop(req_id, None)
        await callback_query.message.delete()
        await callback_query.answer("❌ Request cancelled.")
        return

    # --- Download button (dl_<req_id>_<bitrate>) ---
    if data.startswith("dl_"):
        parts = data.split("_")
        if len(parts) != 3:
            await callback_query.answer("Invalid callback data.")
            return
        req_id = parts[1]
        bitrate = parts[2]   # "320" or "128"

        request = download_requests.pop(req_id, None)
        if not request:
            await callback_query.answer("⚠️ This request has expired.", show_alert=True)
            return

        song = request["song"]
        await callback_query.answer("⏬ Downloading, please wait...")

        # Update the message to show progress
        await callback_query.message.edit_caption(
            callback_query.message.caption + "\n\n**🔽 Downloading...**",
            reply_markup=None
        )

        # Download using spotdl
        try:
            dl_result = await spotdl.download(song)
            # spotdl.download returns a list of (Song, file_path) tuples
            if dl_result and len(dl_result) == 1:
                path = dl_result[0][1]
            else:
                raise Exception("Download failed – file not created")
        except Exception as e:
            await callback_query.message.edit_caption(
                f"❌ Download failed: {e}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try again", callback_data=data)
                ]])
            )
            return

        # Send the audio file as a Telegram audio message
        thumbnail = None
        if song.album_art_url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(song.album_art_url) as resp:
                        if resp.status == 200:
                            thumbnail = await resp.read()
            except:
                pass

        # Clean up the old preview message
        await callback_query.message.delete()

        # Send the audio
        try:
            await client.send_audio(
                chat_id=callback_query.message.chat.id,
                audio=open(path, "rb"),
                title=song.name,
                performer=song.artist,
                duration=song.duration,
                thumb=thumbnail if thumbnail else None,
                caption=f"🎵 **{song.name}**\n👤 {song.artist}\n💿 {song.album_name}\n\n"
                        f"✨ Downloaded by @{(await client.get_me()).username}",
                file_name=f"{song.artist} - {song.name}.mp3"
            )
        finally:
            # Remove the temporary file
            try:
                os.remove(path)
            except:
                pass

        await callback_query.answer("✅ Sent!")
