import asyncio
import logging
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

logger = logging.getLogger(__name__)

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

# ── Song metadata sanitiser ────────────────────────────────────────────────────

class _MissingType:
    pass

_MISSING = _MissingType()


def _sanitise_song(song) -> None:
    """Patch missing/None metadata fields on a spotdl Song in-place (Fix #3)."""
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
            pass  # frozen dataclass – best-effort only

# ── DRM error type ─────────────────────────────────────────────────────────────

class DRMProtectedError(RuntimeError):
    """Raised when the requested track is DRM-protected."""

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — spotdl
# ═══════════════════════════════════════════════════════════════════════════════

async def download_spotify(song, output_format: str, quality: str) -> Path:
    """
    Download a pre-resolved spotdl Song object.
    Fixes #3 (genres) and #5 (None path).
    """
    _sanitise_song(song)

    kwargs: dict = {"output_format": output_format}
    if output_format in {"mp3", "m4a"} and str(quality).isdigit():
        kwargs["bitrate"] = f"{quality}k"

    try:
        results = await spotdl.download(song, **kwargs)
    except Exception as exc:
        raise RuntimeError(f"spotdl download error: {exc}") from exc

    if not results:
        raise RuntimeError("spotdl returned an empty result list.")

    _, file_path = results[0]

    if file_path is None:
        raise RuntimeError(
            "spotdl could not save the file. "
            "The track may be unavailable in your region or the output directory is not writable."
        )

    resolved = Path(file_path)
    if not resolved.exists():
        raise RuntimeError(f"Expected output file not found on disk: {resolved}")

    return resolved


async def download_spotdl_by_url(url: str, output_format: str, quality: str) -> Path:
    """Search + download via spotdl (used by the unified entry-point)."""
    try:
        songs = await spotdl.search([url])
    except Exception as exc:
        raise RuntimeError(f"spotdl search failed: {exc}") from exc

    if not songs:
        raise RuntimeError("spotdl could not find any tracks for that URL.")

    return await download_spotify(songs[0], output_format, quality)

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — yt-dlp
# ═══════════════════════════════════════════════════════════════════════════════

_YT_COOKIE_FILE: str = "plugins/mnbot/youtube.txt"
_YT_COOKIE_BROWSER: str | None = None  # using cookie file; no browser needed


def _build_ydl_opts(output_format: str, quality: str) -> dict:
    opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": "downloads/%(title).100s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "skip": ["hls", "dash"],
                "player_skip": ["configs"],
            }
        },
    }

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


def _sync_extract(ydl_opts: dict, url: str):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def download_generic(url: str, output_format: str, quality: str):
    """Download via yt-dlp. Returns (info_dict, audio_path_str)."""
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
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            raise RuntimeError(
                "YouTube requires authentication for this video.\n"
                "Add your Netscape cookies to plugins/mnbot/youtube.txt and restart the bot.\n"
                "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
            ) from exc
        if "[DRM]" in msg:
            raise DRMProtectedError(
                "This track is DRM-protected and cannot be downloaded."
            ) from exc
        raise RuntimeError(f"Download failed: {msg}") from exc

    if info is None:
        raise RuntimeError("No downloadable audio was extracted.")

    filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
    audio_path = (
        filename.rsplit(".", 1)[0] + f".{output_format}"
        if output_format in {"mp3", "m4a"}
        else filename
    )
    return info, audio_path

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — SpotiFLAC API
#
# How it works (mirroring the FastAPI server's own logic):
#   1. POST /api/download  → receive job_id
#   2. Poll  GET /api/status/{job_id} every POLL_INTERVAL seconds
#      until status == "completed" or "failed" (or timeout)
#   3. The server writes FLAC files to MUSIC_DIR on its own filesystem.
#      We fetch each file over HTTP via a companion /files/ static route
#      (see SPOTIFLAC_FILE_BASE_URL) and save it locally under downloads/.
#
# Required env vars (set in your .env / Docker environment):
#   SPOTIFLAC_API_URL      — e.g. http://localhost:9118
#   SPOTIFLAC_FILE_BASE_URL — e.g. http://localhost:9118/files
#                            The server must serve MUSIC_DIR at this path.
#                            If the bot and API share a filesystem you can
#                            set SPOTIFLAC_SHARED_FS=1 and skip the HTTP fetch.
# ═══════════════════════════════════════════════════════════════════════════════

_SPOTIFLAC_API_URL: str | None = os.getenv("SPOTIFLAC_API_URL")          # http://host:9118
_SPOTIFLAC_FILE_BASE: str | None = os.getenv("SPOTIFLAC_FILE_BASE_URL")  # http://host:9118/files
_SPOTIFLAC_SHARED_FS: bool = os.getenv("SPOTIFLAC_SHARED_FS", "0") == "1"

_POLL_INTERVAL: float = 5.0    # seconds between status checks
_POLL_TIMEOUT: float  = 600.0  # give up after 10 minutes


async def _spotiflac_available() -> bool:
    """Quick health-check so we skip the fallback when the server is down."""
    if not _SPOTIFLAC_API_URL:
        return False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_SPOTIFLAC_API_URL}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                return r.status == 200
    except Exception:
        return False


async def download_spotiflac(url: str, output_format: str) -> Path:
    """
    Submit a download job to the SpotiFLAC API, poll until done,
    then return a local Path to the downloaded FLAC (or converted) file.

    output_format is noted for the caller but SpotiFLAC always produces FLAC;
    conversion happens after this function returns if the caller needs MP3/M4A.
    """
    if not _SPOTIFLAC_API_URL:
        raise RuntimeError("SPOTIFLAC_API_URL is not configured.")

    # ── 1. Submit job ──────────────────────────────────────────────────────────
    payload = {
        "url": url,
        "output_subdir": "bot_downloads",
        "services": ["qobuz", "amazon", "tidal"],  # API's own priority order
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_SPOTIFLAC_API_URL}/api/download",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"SpotiFLAC API rejected request ({resp.status}): {text[:300]}")
            data = await resp.json()

    job_id: str = data["job_id"]
    logger.info("SpotiFLAC job queued: %s for %s", job_id, url)

    # ── 2. Poll until completed / failed / timeout ─────────────────────────────
    deadline = asyncio.get_event_loop().time() + _POLL_TIMEOUT
    files: list[str] = []

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(_POLL_INTERVAL)

            async with session.get(
                f"{_SPOTIFLAC_API_URL}/api/status/{job_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                status_data = await resp.json()

            status = status_data.get("status")
            logger.debug("SpotiFLAC job %s: %s", job_id, status)

            if status == "completed":
                files = status_data.get("files", [])
                break

            if status == "failed":
                error = status_data.get("error") or "unknown error"
                raise RuntimeError(f"SpotiFLAC job failed: {error[:300]}")

            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError(
                    f"SpotiFLAC job {job_id} timed out after {_POLL_TIMEOUT:.0f}s."
                )

    if not files:
        raise RuntimeError("SpotiFLAC job completed but returned no files.")

    # ── 3. Obtain the first FLAC file ──────────────────────────────────────────
    remote_path = files[0]  # absolute path on the API server's filesystem

    Path("downloads").mkdir(exist_ok=True)
    local_path = Path("downloads") / Path(remote_path).name

    if _SPOTIFLAC_SHARED_FS:
        # Bot and API share the same filesystem — just use the path directly
        resolved = Path(remote_path)
        if not resolved.exists():
            raise RuntimeError(f"Shared-FS: file not found at {remote_path}")
        return resolved

    # Fetch over HTTP using the file-serving base URL
    if not _SPOTIFLAC_FILE_BASE:
        raise RuntimeError(
            "SPOTIFLAC_FILE_BASE_URL is not set. "
            "Either set it to the URL where MUSIC_DIR is served, "
            "or set SPOTIFLAC_SHARED_FS=1 if the bot shares the filesystem."
        )

    # Build download URL: strip MUSIC_DIR prefix to get the relative path
    music_dir = os.getenv("SPOTIFLAC_MUSIC_DIR", "/music")
    try:
        rel = Path(remote_path).relative_to(music_dir)
    except ValueError:
        rel = Path(remote_path).name  # fallback: just the filename

    file_url = f"{_SPOTIFLAC_FILE_BASE.rstrip('/')}/{rel}"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            file_url, timeout=aiohttp.ClientTimeout(total=300)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Could not fetch file from SpotiFLAC server ({resp.status}): {file_url}"
                )
            data = await resp.read()

    local_path.write_bytes(data)
    logger.info("SpotiFLAC: saved %s (%d bytes)", local_path, len(data))
    return local_path

# ═══════════════════════════════════════════════════════════════════════════════
# Unified entry-point  (spotdl → yt-dlp → SpotiFLAC)
# ═══════════════════════════════════════════════════════════════════════════════

async def download(url: str, output_format: str, quality: str) -> dict:
    """
    Try each downloader in order and return the first that succeeds.

    Return dict:
        path      : Path  – local file
        title     : str
        artist    : str
        thumb_url : str | None
        source    : str   – which backend delivered the file
    """
    errors: list[str] = []

    # ── Stage 1: spotdl ────────────────────────────────────────────────────────
    if uses_spotdl(url) and spotdl:
        try:
            file_path = await download_spotdl_by_url(url, output_format, quality)
            logger.info("Downloaded via spotdl: %s", file_path)
            return {
                "path": file_path,
                "title": file_path.stem,
                "artist": "",
                "thumb_url": None,
                "source": "spotdl",
            }
        except DRMProtectedError:
            raise  # DRM is fatal – don't fall through
        except Exception as exc:
            errors.append(f"spotdl: {exc}")
            logger.warning("spotdl failed, trying yt-dlp. Reason: %s", exc)

    # ── Stage 2: yt-dlp ────────────────────────────────────────────────────────
    try:
        info, audio_path = await download_generic(url, output_format, quality)
        logger.info("Downloaded via yt-dlp: %s", audio_path)
        return {
            "path": Path(audio_path),
            "title": info.get("title", "Unknown"),
            "artist": info.get("uploader", ""),
            "thumb_url": info.get("thumbnail"),
            "source": "yt-dlp",
        }
    except DRMProtectedError:
        raise  # DRM is fatal
    except Exception as exc:
        errors.append(f"yt-dlp: {exc}")
        logger.warning("yt-dlp failed, trying SpotiFLAC. Reason: %s", exc)

    # ── Stage 3: SpotiFLAC API ─────────────────────────────────────────────────
    if not await _spotiflac_available():
        errors.append("SpotiFLAC: server unreachable or SPOTIFLAC_API_URL not set")
    else:
        try:
            file_path = await download_spotiflac(url, output_format)
            logger.info("Downloaded via SpotiFLAC: %s", file_path)
            return {
                "path": file_path,
                "title": file_path.stem,
                "artist": "",
                "thumb_url": None,
                "source": "spotiflac",
            }
        except Exception as exc:
            errors.append(f"SpotiFLAC: {exc}")
            logger.error("SpotiFLAC also failed: %s", exc)

    # ── All stages exhausted ───────────────────────────────────────────────────
    summary = "\n".join(f"  • {e}" for e in errors)
    raise RuntimeError(f"All download methods failed:\n{summary}")
