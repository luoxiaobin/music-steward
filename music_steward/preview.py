"""Listenable previews for audio a browser cannot play (APE, WavPack, TTA, WMA, DTS ...) and for
single tracks inside a cue image: FFmpeg converts just that file or slice to a small MP3, kept
in a size-limited cache (~/.music-steward/preview). The source is only read.

The preview is complete before it is served, so the player can seek in it.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path

MAX_CACHE_BYTES = int(os.environ.get("MLT_PREVIEW_CACHE_MB", "1024")) * 1024 * 1024
MAX_SECONDS = 20 * 60          # a preview never needs more than 20 minutes
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def cache_dir() -> Path:
    if os.environ.get("MLT_PREVIEW_DIR"):
        return Path(os.environ["MLT_PREVIEW_DIR"])
    from .db import APP_DIR
    return APP_DIR / "preview"


class PreviewError(Exception):
    pass


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _prune(keep: Path):
    files = sorted((p for p in cache_dir().glob("*.mp3")), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        if total <= MAX_CACHE_BYTES:
            break
        if p != keep:
            total -= p.stat().st_size
            p.unlink(missing_ok=True)


def make(src: str, start: float | None = None, duration: float | None = None, offset: int = 0) -> Path:
    """Path of an MP3 preview of `src` (or of its slice start..start+duration), made if needed."""
    st = os.stat(src)
    if start is not None and (start < 0 or start > 24 * 3600):
        raise PreviewError("bad start")
    dur = min(duration, MAX_SECONDS) if duration and duration > 0 else MAX_SECONDS
    key = hashlib.sha256(f"{src}|{st.st_size}|{st.st_mtime_ns}|{start}|{dur}".encode()).hexdigest()[:24]
    out = cache_dir() / f"{key}.mp3"
    with _lock(key):
        if out.exists() and out.stat().st_size > 0:
            os.utime(out)                       # most recently used
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mp3")
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
        if offset:
            cmd += ["-skip_initial_bytes", str(offset)]   # APE with an ID3 block in front
        if start:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-t", f"{dur:.3f}", "-i", src, "-map", "0:a:0", "-vn", "-ac", "2",
                "-c:a", "libmp3lame", "-q:a", "4", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise PreviewError((r.stderr.decode(errors="replace").strip().splitlines() or ["conversion failed"])[-1][:200])
        os.replace(tmp, out)
    _prune(out)
    return out
