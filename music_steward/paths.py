"""Path handling: NFC keys, file kinds."""
from __future__ import annotations

import os
import unicodedata

AUDIO_EXTS = {".mp3", ".flac", ".ape", ".m4a", ".mp4", ".aac", ".wav", ".wma",
              ".ogg", ".opus", ".aif", ".aiff", ".dsf", ".dff", ".wv", ".tta"}
SIDE_EXTS = {".cue": "cue", ".lrc": "lrc"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
# never interesting, never reported
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "@eaDir", "#recycle", ".AppleDouble",
              "_ORIGINALS"}      # where retired originals are parked (retire.py): never scanned as music


def nfc(s: str) -> str:
    """macOS may hand back NFD names; every key we store is NFC."""
    return unicodedata.normalize("NFC", s)


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def kind_of(name: str) -> str:
    e = ext_of(name)
    if e in AUDIO_EXTS:
        return "audio"
    if e in SIDE_EXTS:
        return SIDE_EXTS[e]
    if e in IMAGE_EXTS:
        return "image"
    return "other"


def is_junk(name: str) -> bool:
    return name in JUNK_NAMES or name.startswith("._")
