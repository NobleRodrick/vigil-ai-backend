"""
VIGIL-AI Cameroun — Remote Media Fetcher

Downloads media referenced by URL so it can be analyzed by the detection
engine. Two strategies:

  1. Direct download (httpx, streaming, size-capped) for URLs that serve an
     image/audio/video content type directly.
  2. yt-dlp (optional dependency) for platform URLs — YouTube, Facebook,
     TikTok, X, Dailymotion, Vimeo — with duration and filesize caps suited
     to the free-tier deployment.

All functions are failure-tolerant: they return None rather than raising,
so the analysis pipeline degrades gracefully when a URL is unreachable.
"""
import asyncio
import logging
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PLATFORM_DOMAINS = (
    "youtube.com", "youtu.be", "facebook.com", "fb.watch", "twitter.com",
    "x.com", "tiktok.com", "dailymotion.com", "vimeo.com", "instagram.com",
)

_EXT_BY_KIND = {"image": ".jpg", "audio": ".mp3", "video": ".mp4"}

_MIME_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "audio/mpeg": ".mp3", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
}


def is_platform_url(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
    except ValueError:
        return False
    return any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)


def _download_dir(kind: str) -> Path:
    d = Path(settings.UPLOAD_DIR) / kind / "fetched"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def fetch_direct(url: str, kind: str) -> str | None:
    """Stream a direct media URL to local storage. Returns the file path,
    or None if unreachable / wrong type / too large."""
    max_bytes = settings.URL_FETCH_MAX_MB * 1024 * 1024
    # A full browser UA — many CDNs (incl. Wikimedia) 403 bare bot agents
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 VIGIL-AI/1.0"
        ),
        "Accept": "*/*",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.URL_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    logger.warning(f"Media fetch got HTTP {resp.status_code} for {url}")
                    return None

                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if not content_type.startswith(f"{kind}/"):
                    # Some CDNs serve application/octet-stream — accept if the
                    # URL extension matches the expected kind.
                    ext = Path(urlparse(url).path).suffix.lower()
                    if content_type != "application/octet-stream" or not ext:
                        logger.warning(
                            f"Media fetch expected {kind}/*, got '{content_type}' for {url}"
                        )
                        return None

                declared = resp.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    logger.warning(f"Media at {url} exceeds {settings.URL_FETCH_MAX_MB}MB cap")
                    return None

                ext = _MIME_EXT.get(content_type) or Path(urlparse(url).path).suffix.lower() or _EXT_BY_KIND[kind]
                dest = _download_dir(kind) / f"{uuid.uuid4().hex}{ext}"

                received = 0
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        received += len(chunk)
                        if received > max_bytes:
                            f.close()
                            dest.unlink(missing_ok=True)
                            logger.warning(f"Media at {url} exceeded size cap mid-download")
                            return None
                        f.write(chunk)

                logger.info(f"Fetched {kind} from URL ({received} bytes): {dest}")
                return str(dest)
    except (httpx.RequestError, OSError, ValueError) as e:
        logger.warning(f"Direct media fetch failed for {url}: {e}")
        return None


def _fetch_video_ytdlp_sync(url: str) -> str | None:
    """Download a platform video with yt-dlp (blocking — call via thread)."""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        logger.info("yt-dlp not installed — platform video download unavailable")
        return None

    dest_dir = _download_dir("video")
    out_id = uuid.uuid4().hex
    outtmpl = str(dest_dir / f"{out_id}.%(ext)s")
    max_bytes = settings.YTDLP_MAX_FILESIZE_MB * 1024 * 1024

    ydl_opts = {
        "outtmpl": outtmpl,
        # Prefer a small progressive mp4 so no ffmpeg merge step is needed
        "format": (
            f"best[ext=mp4][filesize<{settings.YTDLP_MAX_FILESIZE_MB}M]"
            "/best[ext=mp4]/worst[ext=mp4]/best"
        ),
        "max_filesize": max_bytes,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration <= {settings.YTDLP_MAX_DURATION_SECONDS}"
        ),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        matches = list(dest_dir.glob(f"{out_id}.*"))
        if matches:
            logger.info(f"yt-dlp downloaded video: {matches[0]}")
            return str(matches[0])
        logger.warning(f"yt-dlp produced no file for {url} (filtered by duration/size?)")
        return None
    except Exception as e:
        logger.warning(f"yt-dlp download failed for {url}: {e}")
        return None


async def fetch_video(url: str) -> str | None:
    """Fetch a video URL: platform URLs via yt-dlp, direct URLs via HTTP."""
    if is_platform_url(url):
        if not settings.YTDLP_ENABLED:
            return None
        return await asyncio.to_thread(_fetch_video_ytdlp_sync, url)
    # Direct link first; fall back to yt-dlp which handles many edge cases
    path = await fetch_direct(url, "video")
    if path:
        return path
    if settings.YTDLP_ENABLED:
        return await asyncio.to_thread(_fetch_video_ytdlp_sync, url)
    return None
