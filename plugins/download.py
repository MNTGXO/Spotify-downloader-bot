import asyncio
import os
import re
import uuid
from functools import partial
from pathlib import Path

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

# ── URL classifiers ────────────────────────────────────────────────────────────

def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url or "spotify.link" in url

def is_apple_music_url(url: str) -> bool:
    return "music.apple.com" in url

def is_deezer_url(url: str) -> bool:
    return "deezer.com" in url

def uses_spotdl(url: str) -> bool:
    """Routes that spotdl handles natively (Spotify + Apple Music)."""
    return is_spotify_url(url) or is_apple_music_url(url)

# ── Telegram handler ───────────────────────────────────────────────────────────

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

# ── Thumbnail helper ───────────────────────────────────────────────────────────

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

# ── FIX #3 & #5 — Spotify / Apple Music via spotdl ────────────────────────────
#
# Fix #3: spotdl's Song object may have genres=None (or other missing fields).
#         We patch the download call inside a try/except and normalise the Song
#         metadata before passing it on, so a missing 'genres' key never bubbles
#         up as an unhandled KeyError / TypeError.
#
# Fix #5: spotdl.download() returns List[Tuple[Song, Optional[Path]]].
#         The second element is None when the track could not be saved to disk.
#         We must check for that instead of blindly indexing result[0][1].

async def download_spotdl(url: str, output_format: str, quality: str) -> Path:
    """
    Download a Spotify or Apple Music URL with spotdl.
    Returns the Path of the downloaded file.
    Raises RuntimeError with a human-readable message on failure.
    """
    from spotdl.types.song import Song  # local import to avoid circular deps

    kwargs: dict = {"output_format": output_format}
    if output_format in {"mp3", "m4a"} and quality.isdigit():
        kwargs["bitrate"] = f"{quality}k"

    # ---- search / resolve the song object first --------------------------------
    try:
        songs = await spotdl.search([url])
    except Exception as exc:
        raise RuntimeError(f"spotdl search failed: {exc}") from exc

    if not songs:
        raise RuntimeError("spotdl could not find any tracks for that URL.")

    downloaded_paths: list[Path] = []

    for song in songs:
        # Fix #3 — sanitise metadata fields that may be None / missing
        _sanitise_song(song)

        try:
            results = await spotdl.download(song, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"spotdl download error: {exc}") from exc

        # Fix #5 — results is List[Tuple[Song, Optional[Path]]]
        if not results:
            raise RuntimeError("spotdl returned an empty result list.")

        _, file_path = results[0]

        if file_path is None:
            raise RuntimeError(
                "spotdl could not save the file. "
                "The track may be unavailable in your region or the output directory is not writable."
            )

        if not Path(file_path).exists():
            raise RuntimeError(f"Expected output file not found on disk: {file_path}")

        downloaded_paths.append(Path(file_path))

    # Return first (or only) downloaded path
    return downloaded_paths[0]


def _sanitise_song(song) -> None:
    """
    Patch missing / None metadata fields on a spotdl Song object in-place.
    Prevents downstream KeyError / TypeError crashes (Fix #3).
    """
    defaults = {
        "genres": [],
        "disc_number": 1,
        "disc_count": 1,
        "copyright_text": "",
        "download_url": None,
        "lyrics": None,
        "popularity": 0,
        "album_id": "",
        "album_artist": "",
    }
    for field, default in defaults.items():
        try:
            val = getattr(song, field, _MISSING)
            if val is _MISSING or val is None:
                object.__setattr__(song, field, default)
        except Exception:
            pass  # dataclass may be frozen; best-effort only

class _MissingType:
    pass
_MISSING = _MissingType()

# ── FIX #4 — Deezer DRM guard ─────────────────────────────────────────────────
#
# yt-dlp raises DownloadError with "[DRM]" in the message for Deezer tracks.
# We catch it early and surface a clean message instead of a raw traceback.

class DRMProtectedError(RuntimeError):
    """Raised when the requested track is DRM-protected."""

# ── FIX #1 — YouTube cookie + fallback extractor support ──────────────────────
#
# yt-dlp throws "Sign in to confirm you're not a bot" when YouTube blocks the
# request.  We add:
#   • cookiefile  – optional path read from env var YT_COOKIE_FILE
#   • cookiesfrombrowser – optional browser name from env YT_COOKIE_BROWSER
#   • extractor_args to bypass the consent page
#   • A graceful retry hint in the error message

_YT_COOKIE_FILE: str = "plugins/mnbot/youtube.txt"
_YT_COOKIE_BROWSER: str | None = None  # not needed; using cookie file


def _build_ydl_opts(output_format: str, quality: str) -> dict:
    opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": "downloads/%(title).100s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Fix #1 – bypass age/bot checks
        "extractor_args": {
            "youtube": {
                "skip": ["hls", "dash"],          # prefer direct streams
                "player_skip": ["configs"],
            }
        },
    }

    # Fix #1 – attach cookies when available
    if _YT_COOKIE_FILE and os.path.isfile(_YT_COOKIE_FILE):
        opts["cookiefile"] = _YT_COOKIE_FILE
    elif _YT_COOKIE_BROWSER:
        opts["cookiesfrombrowser"] = (_YT_COOKIE_BROWSER,)

    if output_format in {"mp3", "m4a"}:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": output_format,
                "preferredquality": quality if quality.isdigit() else "0",
            }
        ]

    return opts


async def download_generic(url: str, output_format: str, quality: str):
    # Fix #4 – reject Deezer before even trying yt-dlp
    if is_deezer_url(url):
        raise DRMProtectedError(
            "Deezer tracks are DRM-protected and cannot be downloaded. "
            "Please use a Spotify or YouTube link instead."
        )

    ydl_opts = _build_ydl_opts(output_format, quality)

    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, partial(_sync_extract, ydl_opts, url))
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        # Fix #1 – surface actionable YouTube bot-detection hint
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            raise RuntimeError(
                "YouTube requires authentication for this video.\n\n"
                "Set one of these environment variables and restart the bot:\n"
                "  • YT_COOKIE_FILE=/path/to/cookies.txt\n"
                "  • YT_COOKIE_BROWSER=chrome  (or firefox / brave / etc.)\n\n"
                "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
            ) from exc
        # Fix #4 – DRM error from yt-dlp itself
        if "[DRM]" in msg:
            raise DRMProtectedError(
                "This track is DRM-protected and cannot be downloaded."
            ) from exc
        raise RuntimeError(f"Download failed: {msg}") from exc

    if info is None:
        raise RuntimeError("No downloadable audio was extracted.")

    filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
    if output_format in {"mp3", "m4a"}:
        audio_path = filename.rsplit(".", 1)[0] + f".{output_format}"
    else:
        audio_path = filename
    return info, audio_path


def _sync_extract(ydl_opts: dict, url: str):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)

# ── Unified download entry-point ───────────────────────────────────────────────

async def download(url: str, output_format: str, quality: str):
    """
    Route to the correct downloader and return a consistent result dict:
      {
        "path":     Path,
        "title":    str,
        "artist":   str,
        "thumb_url": str | None,
      }
    Raises RuntimeError or DRMProtectedError on failure.
    """
    if uses_spotdl(url):
        file_path = await download_spotdl(url, output_format, quality)
        return {
            "path": file_path,
            "title": file_path.stem,
            "artist": "",
            "thumb_url": None,
        }
    else:
        info, audio_path = await download_generic(url, output_format, quality)
        return {
            "path": Path(audio_path),
            "title": info.get("title", "Unknown"),
            "artist": info.get("uploader", ""),
            "thumb_url": info.get("thumbnail"),
        }
