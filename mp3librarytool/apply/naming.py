"""Destination names: from the accepted release, safe on macOS AND Windows.

Both machines see the same share, so names avoid Windows' reserved characters and
names (CON, NUL, ...), trailing dots/spaces, and are NFC-normalized. Chinese passes
through unchanged. The extension comes from the real codec (APE named .mp3 -> .ape).
"""
from __future__ import annotations

import re
import unicodedata

CODEC_EXT = {"mp3": ".mp3", "mp2": ".mp2", "flac": ".flac", "ape": ".ape", "aac": ".m4a", "alac": ".m4a",
             "wmav2": ".wma", "wmav1": ".wma", "pcm_s16le": ".wav", "pcm_s24le": ".wav", "vorbis": ".ogg",
             "opus": ".opus", "wavpack": ".wv", "dts": ".dts"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_COMPONENT = 120  # characters; keeps full paths well under Windows' 260 limit in practice


def safe_component(s: str | None, fallback: str = "Unknown") -> str:
    s = unicodedata.normalize("NFC", (s or "").strip()) or fallback
    s = BAD.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    if s.split(".")[0].upper() in RESERVED:
        s = "_" + s
    if len(s) > MAX_COMPONENT:
        s = s[:MAX_COMPONENT].rstrip(". ")
    return s or fallback


def ext_for(codec: str | None, source_ext: str) -> str:
    return CODEC_EXT.get(codec or "", source_ext.lower())


def album_dir(albumartist: str | None, album: str | None, year: int | None) -> str:
    a = safe_component(albumartist, "Unknown Artist")
    b = safe_component(f"{album} ({year})" if album and year else album, "Unknown Album")
    return f"{a}/{b}"


def track_name(title: str | None, index: int | None, medium: int | None, mediums: int | None,
               codec: str | None, source_ext: str) -> str:
    num = f"{index:02d}" if index else "00"
    if mediums and mediums > 1 and medium:
        num = f"{medium}-{num}"
    return safe_component(f"{num} - {title or 'Untitled'}") + ext_for(codec, source_ext)


def collision_key(relpath: str) -> str:
    """Two destinations clash if they differ only by case or Unicode form (macOS/Windows)."""
    return unicodedata.normalize("NFC", relpath).casefold()
