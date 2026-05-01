import os

from pyrogram import Client
from pyrogram.types import CallbackQuery

from plugins.download import (
    DRMProtectedError,
    download_generic,
    download_spotify,
    fetch_thumb,
    is_spotify_url,
    is_apple_music_url,
    pending_requests,
    uses_spotdl,
)
from plugins.spotify_client import spotdl


@Client.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    data = callback_query.data or ""

    if data == "help":
        await callback_query.answer()
        await callback_query.message.edit_text("Use /help for full instructions.")
        return

    if data == "about":
        await callback_query.answer()
        await callback_query.message.edit_text(
            "🎵 Advanced Music Downloader Bot\nPowered by Pyrogram, spotdl & yt-dlp."
        )
        return

    if not data.startswith("d_"):
        await callback_query.answer("Unknown action", show_alert=True)
        return

    _, output_format, quality, req_id = data.split("_", 3)
    request = pending_requests.pop(req_id, None)
    if not request:
        await callback_query.answer("Request expired. Send the link again.", show_alert=True)
        return

    url = request["url"]
    await callback_query.answer("Downloading...")
    await callback_query.message.edit_text("⏬ Downloading, please wait...")

    path = None
    try:
        if uses_spotdl(url):
            # Covers both Spotify and Apple Music
            if not spotdl:
                raise RuntimeError("Spotify/Apple Music credentials are missing on the server.")

            songs = await spotdl.search([url])
            if not songs:
                raise RuntimeError("No track found for this URL.")

            song = songs[0]
            use_format = "mp3" if output_format == "best" else output_format
            use_quality = "320" if quality == "best" else quality

            path = await download_spotify(song, use_format, use_quality)
            title = song.name
            performer = song.artist
            duration = int(song.duration or 0)
            thumb = await fetch_thumb(song.album_art_url)

        else:
            info, path = await download_generic(url, output_format, quality)
            title = info.get("title", "Unknown")
            performer = info.get("uploader", "Unknown")
            duration = int(info.get("duration", 0) or 0)
            thumb = await fetch_thumb(info.get("thumbnail"))

        ext = os.path.splitext(str(path))[1].lstrip(".") or "audio"
        caption = f"🎵 **{title}**\n👤 {performer}\n📦 {ext.upper()}"

        await client.send_audio(
            chat_id=callback_query.message.chat.id,
            audio=str(path),
            title=title,
            performer=performer,
            duration=duration,
            thumb=thumb,
            caption=caption,
            file_name=f"{performer} - {title}.{ext}",
        )
        await callback_query.message.delete()

    except DRMProtectedError as exc:
        await callback_query.message.edit_text(f"🔒 {exc}")

    except Exception as exc:
        await callback_query.message.edit_text(f"❌ Download failed: {exc}")

    finally:
        try:
            if path and os.path.exists(str(path)):
                os.remove(str(path))
        except Exception:
            pass
