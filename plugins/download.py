import re, uuid, os, asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from plugins.spotify_client import spotdl
import yt_dlp
import aiohttp
from functools import partial

download_requests = {}

URL_REGEX = re.compile(
    r'https?://(?:www\.)?(?:'
    r'open\.spotify\.com/|'
    r'spotify\.link/|'
    r'music\.youtube\.com/|'
    r'youtube\.com/|'
    r'youtu\.be/|'
    r'soundcloud\.com/|'
    r'deezer\.com/|'
    r'music\.apple\.com/'
    r').*',
    re.IGNORECASE
)

def is_spotify_url(url: str) -> bool:
    return any(domain in url for domain in ["open.spotify.com", "spotify.link"])

@Client.on_message(filters.text & filters.regex(URL_REGEX))
async def on_music_link(client, message):
    url = message.matches[0].group()
    if is_spotify_url(url):
        await handle_spotify_link(client, message, url)
    else:
        await handle_ytdlp_fallback(client, message, url)

async def handle_spotify_link(client, message, url):
    if not spotdl:
        await message.reply_text("❌ Spotify credentials not configured. Only non‑Spotify links are supported.")
        return

    try:
        songs = await spotdl.search([url])
    except Exception as e:
        await message.reply_text(f"⚠️ Could not fetch track info: {e}")
        return

    if not songs:
        await message.reply_text("❌ No track found at that link.")
        return

    if len(songs) > 1:
        await message.reply_text(
            f"📁 **Playlist/Album with {len(songs)} tracks found.**\n"
            "Tap a track to download it.",
            reply_markup=build_playlist_keyboard(songs)
        )
        return

    song = songs[0]
    req_id = uuid.uuid4().hex
    download_requests[req_id] = {"song": song}

    caption = (
        f"🎵 **{song.name}**\n"
        f"👤 {song.artist}\n"
        f"💿 {song.album_name} ({song.date.year if song.date else '?'})\n"
        f"⏱ {format_duration(song.duration) if song.duration else 'N/A'}\n\n"
        "📦 High‑quality **m4a** (AAC)"
    )

    thumb = None
    if song.album_art_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(song.album_art_url) as resp:
                    if resp.status == 200:
                        thumb = await resp.read()
        except:
            pass

    sent = await message.reply_photo(
        photo=thumb if thumb else "https://i.imgur.com/CqXrA0A.png",
        caption=caption,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎧 Download m4a", callback_data=f"dl_{req_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{req_id}")]
        ])
    )
    download_requests[req_id]["message_id"] = sent.id
    download_requests[req_id]["chat_id"] = message.chat.id

def build_playlist_keyboard(songs, per_page=10):
    buttons = []
    for idx, song in enumerate(songs[:per_page]):
        req_id = uuid.uuid4().hex
        download_requests[req_id] = {"song": song}
        buttons.append([InlineKeyboardButton(
            f"{idx+1}. {song.name[:35]}...",
            callback_data=f"dl_{req_id}"
        )])
    return InlineKeyboardMarkup(buttons)

def format_duration(seconds):
    if not seconds:
        return "0:00"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}:{secs:02d}"

# ── yt-dlp fallback (still m4a) ──

async def handle_ytdlp_fallback(client, message, url):
    progress_msg = await message.reply_text("🔍 **Analyzing link...**")

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': 'downloads/%(title).100s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',
            'preferredquality': '320',
        }],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }

    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(
            None,
            partial(_sync_extract, ydl_opts, url)
        )
    except Exception as e:
        await progress_msg.edit_text(f"❌ Failed to process link: {e}")
        return

    if info is None:
        await progress_msg.edit_text("❌ Could not extract any audio from this link.")
        return

    filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
    audio_path = filename.rsplit('.', 1)[0] + '.m4a'

    await progress_msg.edit_text("📤 **Uploading...**")

    thumb = None
    thumbnail_url = info.get('thumbnail')
    if thumbnail_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail_url) as resp:
                    if resp.status == 200:
                        thumb = await resp.read()
        except:
            pass

    bot_username = (await client.get_me()).username
    try:
        await client.send_audio(
            chat_id=message.chat.id,
            audio=audio_path,
            title=info.get('title', 'Unknown'),
            performer=info.get('uploader', 'Unknown'),
            duration=int(info.get('duration', 0)),
            thumb=thumb,
            caption=f"🎵 **{info.get('title', 'Unknown')}**\n👤 {info.get('uploader', 'Unknown')}\n✨ Downloaded by @{bot_username}",
            file_name=f"{info.get('uploader', 'Unknown')} - {info.get('title', 'Unknown')}.m4a"
        )
    finally:
        try:
            os.remove(audio_path)
        except:
            pass

    await progress_msg.delete()

def _sync_extract(ydl_opts, url):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)
