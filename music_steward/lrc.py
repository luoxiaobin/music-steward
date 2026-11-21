"""Timed lyrics (.lrc): split an album-long file along cue-sheet tracks. Read-only on the source.

An image album may come with one .lrc for the whole image (`image.flac` + `image.lrc`). When the image
is split into tracks, each track needs its own lyrics with times counted from the track's start;
copying the album's file next to track 1 left the other tracks without lyrics (Codex review on #7).

Lines are `[mm:ss.xx]text` (several time tags per line allowed); header tags like [ar:] [al:] [by:]
[offset:] are kept in every part, [ti:] and [length:] are per track and replaced or dropped.
"""
from __future__ import annotations

import re

from .cue import detect_encoding

TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
HEADER = re.compile(r"^\[([a-zA-Z]+):(.*)\]\s*$")


def decode(raw: bytes) -> str:
    enc = detect_encoding(raw)
    return raw.decode(enc, errors="replace").lstrip("﻿")


def _seconds(m) -> float:
    frac = m.group(3) or "0"
    return int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac))


def _stamp(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"[{cs // 6000:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}]"


def split(text: str, bounds: list[tuple[float, float | None]], titles: list[str | None]) -> list[str | None]:
    """One lyrics text per track ([start, end) in seconds into the image), times relative to the
    track's start; None for a track with no timed line."""
    headers, timed = [], []
    for line in text.splitlines():
        h = HEADER.match(line.strip())
        if h and not TIME.match(line.strip()):
            if h.group(1).lower() not in ("ti", "length"):
                headers.append(line.strip())
            continue
        stamps = list(TIME.finditer(line))
        if not stamps:
            continue
        words = line[stamps[-1].end():] if stamps else line
        for m in stamps:
            timed.append((_seconds(m), words))
    out: list[str | None] = []
    for (start, end), title in zip(bounds, titles):
        mine = sorted((t - start, w) for t, w in timed if t >= start and (end is None or t < end))
        if not mine:
            out.append(None)
            continue
        lines = headers + ([f"[ti:{title}]"] if title else []) + [_stamp(t) + w for t, w in mine]
        out.append("\n".join(lines) + "\n")
    return out
