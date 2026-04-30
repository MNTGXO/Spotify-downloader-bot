import re, uuid, asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from plugins.spotify_client import spotdl

# Temporary storage for download requests (in production you may use Redis)
download_requests = {}   # key: request_id -> {"song": Song, "message_id": ..., "chat_id": ...}

# Regular expression to catch supported links
URL_REGEX = re.compile(
    r'https?://(?:www\.)?(?:'
    r'open\.spotify\.com/|'
    r'music\.youtube\.com/|'
    r'youtube\.com/|'
    r'youtu\.be/|'
    r'soundcloud\.com/|'
    r'deezer\.com/|'
    r'music\.apple\.com/'
    r').*',
    re.IGNORECASE
)

@Client.on_message(filters.text & filters.regex(URL_REGEX))
async def on_music_link(client, message):
    url = message.matches[0].group()

    if not spotdl:
        await message.reply_text("❌ Spotify credentials not configured. Only non‑Spotify links are supported.")
        # We could still try yt-dlp here, but keep it simple
        return

    # Search for the song
    try:
        songs = await spotdl.search([url])
    except Exception as e:
        await message.reply_text(f"⚠️ Could not fetch track info: {e}")
        return

    if not songs:
        await message.reply_text("❌ No track found at that link.")
        return

    # For simplicity, handle first track only (playlists will be a list)
    if len(songs) > 1:
        await message.reply_text(
            f"📁 **Playlist/Album with {len(songs)} tracks found.**\n"
            "I'll show each track with a download button.",
            reply_markup=build_playlist_keyboard(songs)
        )
        return

    song = songs[0]
    # Create a unique request id
    req_id = uuid.uuid4().hex
    download_requests[req_id] = {"song": song}

    # Build caption
    caption = (
        f"🎵 **{song.name}**\n"
        f"👤 {song.artist}\n"
        f"💿 {song.album_name} ({song.date.year if song.date else '?'})\n"
        f"⏱ {format_duration(song.duration) if song.duration else 'N/A'}\n\n"
        "Choose quality:"
    )

    # Download album art for thumbnail
    thumb = None
    if song.album_art_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(song.album_art_url) as resp:
                    if resp.status == 200:
                        thumb = await resp.read()
        except:
            pass

    # Send preview with inline buttons
    sent = await message.reply_photo(
        photo=thumb if thumb else "https://i.imgur.com/CqXrA0A.png",  # fallback image
        caption=caption,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎧 Download 320kbps", callback_data=f"dl_{req_id}_320"),
             InlineKeyboardButton("🎧 Download 128kbps", callback_data=f"dl_{req_id}_128")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{req_id}")]
        ])
    )

    download_requests[req_id]["message_id"] = sent.id
    download_requests[req_id]["chat_id"] = message.chat.id

def build_playlist_keyboard(songs, page=0, per_page=10):
    """Inline keyboard for playlists (simplified)."""
    # In a full implementation you would paginate – here we just show first 10.
    buttons = []
    for idx, song in enumerate(songs[:per_page]):
        req_id = uuid.uuid4().hex
        download_requests[req_id] = {"song": song}
        buttons.append([InlineKeyboardButton(
            f"{idx+1}. {song.name[:30]}...",
            callback_data=f"dl_{req_id}_320"
        )])
    return InlineKeyboardMarkup(buttons)

def format_duration(seconds):
    if not seconds:
        return "0:00"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}:{secs:02d}"
