import asyncio
import re
import uuid
from functools import partial

import aiohttp
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.spotify_client import spotdl

pending_requests = {}

URL_REGEX = re.compile(
    r"https?://(?:www\.)?(?:"
    r"open\.spotify\.com/|"
    r"spotify\.link/|"
    r"music\.youtube\.com/|"
    r"youtube\.com/|"
    r"youtu\.be/|"
    r"soundcloud\.com/|"
    r"deezer\.com/|"
    r"music\.apple\.com/"
    r").*",
    re.IGNORECASE,
)


@Client.on_message(filters.text & filters.regex(URL_REGEX))
async def on_music_link(client, message):
    url = message.matches[0].group()
    req_id = uuid.uuid4().hex
    pending_requests[req_id] = {"url": url, "chat_id": message.chat.id}

    await message.reply_text(
        "🎚 Choose output format / quality:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("MP3 320", callback_data=f"d_mp3_320_{req_id}"),
                    InlineKeyboardButton("MP3 128", callback_data=f"d_mp3_128_{req_id}"),
                ],
                [
                    InlineKeyboardButton("M4A High", callback_data=f"d_m4a_256_{req_id}"),
                    InlineKeyboardButton("M4A Low", callback_data=f"d_m4a_128_{req_id}"),
                ],
                [InlineKeyboardButton("Best available", callback_data=f"d_best_best_{req_id}")],
            ]
        ),
    )


def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url or "spotify.link" in url


async def fetch_thumb(url: str):
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        return None
    return None


async def download_spotify(song, output_format: str, quality: str):
    kwargs = {"output_format": output_format}
    if output_format in {"mp3", "m4a"} and quality.isdigit():
        kwargs["bitrate"] = f"{quality}k"
    result = await spotdl.download(song, **kwargs)
    if not result or len(result) != 1:
        raise RuntimeError("spotdl did not return a downloaded file")
    return result[0][1]


async def download_generic(url: str, output_format: str, quality: str):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": "downloads/%(title).100s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    if output_format in {"mp3", "m4a"}:
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": output_format,
                "preferredquality": quality if quality.isdigit() else "0",
            }
        ]

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, partial(_sync_extract, ydl_opts, url))
    if info is None:
        raise RuntimeError("No downloadable audio was extracted")

    filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
    if output_format in {"mp3", "m4a"}:
        audio_path = filename.rsplit(".", 1)[0] + f".{output_format}"
    else:
        audio_path = filename
    return info, audio_path


def _sync_extract(ydl_opts, url):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)
