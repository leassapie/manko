"""Aeon-style leech thumbnails for Mangko.

Priority:
  1. User custom thumb  →  thumbnails/{user_id}.jpg  (/thumb)
  2. Mid-video frame    →  ffmpeg scale 640

Series poster is ONLY used for the separate photo post, never as file thumb.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from mangko.config import get_settings

logger = logging.getLogger("mangko")


def _ffmpeg() -> str | None:
    for name in ("ffmpeg", "xtra"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _ffprobe() -> str | None:
    for name in ("ffprobe", "ffmpeg", "xtra"):
        p = shutil.which(name)
        if p:
            return p
    return None


def user_thumb_path(user_id: int) -> Path:
    return get_settings().thumb_dir / f"{user_id}.jpg"


def create_user_thumb(photo_path: Path, user_id: int) -> Path | None:
    ff = _ffmpeg()
    if not ff:
        logger.warning("ffmpeg not found – cannot save user thumb")
        return None
    settings = get_settings()
    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    out = user_thumb_path(user_id)
    try:
        subprocess.run(
            [
                ff, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(photo_path),
                "-vf", "scale=320:-1",
                "-q:v", "5",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        if out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        logger.warning("create_user_thumb failed: %s", e)
    return None


def download_poster_thumb(poster_url: str, dest_dir: Path) -> Path | None:
    if not poster_url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw = dest_dir / "poster_raw"
    out = dest_dir / "poster_thumb.jpg"
    ff = _ffmpeg()
    try:
        with httpx.Client(timeout=30) as client:
            with client.stream("GET", poster_url) as r:
                if r.status_code != 200:
                    return None
                with open(raw, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=8192):
                        f.write(chunk)
        if ff:
            subprocess.run(
                [
                    ff, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(raw),
                    "-vf", "scale=320:-1",
                    "-q:v", "5",
                    str(out),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            raw.unlink(missing_ok=True)
        else:
            out = raw
        if out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        logger.warning("Poster thumb failed: %s", e)
    return None


def _video_duration(video_path: Path) -> float:
    probe = _ffprobe()
    if not probe:
        return 0.0
    try:
        r = subprocess.run(
            [
                probe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    ff = _ffmpeg()
    if not ff:
        return 0.0
    try:
        r = subprocess.run(
            [ff, "-i", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr or "")
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
    except Exception:
        pass
    return 0.0


def extract_video_thumb(video_path: Path, dest_dir: Path) -> Path | None:
    ff = _ffmpeg()
    if not ff:
        logger.warning("ffmpeg not found – skip video thumb")
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{video_path.stem}_thumb.jpg"
    duration = _video_duration(video_path)
    ss = max(1.0, duration / 2.0) if duration > 0 else 5.0
    if duration > 0 and ss >= duration:
        ss = max(0.5, duration * 0.4)
    try:
        subprocess.run(
            [
                ff, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{ss:.2f}",
                "-i", str(video_path),
                "-vf", "scale=640:-1",
                "-vframes", "1",
                "-q:v", "5",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
        if out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        logger.warning("Video thumb extract failed for %s: %s", video_path.name, e)
    try:
        subprocess.run(
            [
                ff, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video_path),
                "-vf", "scale=640:-1",
                "-vframes", "1",
                "-q:v", "5",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
        if out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        logger.warning("Video thumb fallback failed: %s", e)
    return None


def resolve_doc_thumb(
    video_path: Path,
    user_id: int,
    series_thumb: Path | None = None,
    work_dir: Path | None = None,
) -> str | None:
    ut = user_thumb_path(user_id)
    if ut.exists() and ut.stat().st_size > 0:
        logger.info("thumb: user custom %s", ut)
        return str(ut)

    work = work_dir or video_path.parent
    vthumb = extract_video_thumb(video_path, work)
    if vthumb:
        logger.info("thumb: video frame %s", vthumb)
        return str(vthumb)

    logger.warning("thumb: none (no user thumb / video frame failed)")
    return None
