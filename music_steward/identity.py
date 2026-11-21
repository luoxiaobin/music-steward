"""File identities that follow the content, not the path (ADR 0002). Read-only.

Audio files -- "e1:" identity: SHA-256 of the audio region's length, its first and its last 64 KiB.
The audio region leaves out what a tagger rewrites: an ID3v2 tag or the FLAC metadata blocks in front
(tags, pictures, padding), an APEv2 tag and an ID3v1 tag at the end. So retagging a file keeps its
identity (also when the tag grows and the audio moves), re-encoding or replacing it changes it.

Why the ends only: measured on the NAS (2026-11-20), reading the start and end of each file in folder
order costs ~50 ms per file -- the same as opening it to read its tags -- while slices from the middle
of files cost 4x that (disk seeks). An edit confined to the middle of the audio keeps the identity;
that limitation is accepted in ADR 0002.

Formats whose tags live inside the audio container (WAV chunks, MP4 atoms, WMA) are not taken apart:
a tag edit there changes the identity, and the album simply goes back to review -- the safe direction.

Other small files (cue sheets, lyrics, covers) -- "s1:" identity: SHA-256 of the whole file.
"""
from __future__ import annotations

import hashlib
import os
import struct

EDGE = 64 * 1024
SMALL = 4 * 1024 * 1024          # non-audio files up to this size get a whole-file identity
APE_MAGIC = b"MAC "


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _front(fh, size: int) -> int:
    """Where the audio starts: after an ID3v2 tag and/or the FLAC metadata blocks."""
    fh.seek(0)
    head = fh.read(10)
    pos = 0
    if head[:3] == b"ID3" and len(head) == 10:
        pos = 10 + _syncsafe(head[6:10]) + (10 if head[5] & 0x10 else 0)
        fh.seek(pos)
        after = fh.read(64 * 1024)
        pos += len(after) - len(after.lstrip(b"\x00"))       # some taggers zero-pad past the tag
        fh.seek(pos)
        head = fh.read(4)
    if head[:4] == b"fLaC":
        pos += 4
        while pos < size:
            fh.seek(pos)
            h = fh.read(4)
            if len(h) < 4:
                break
            pos += 4 + struct.unpack(">I", b"\0" + h[1:])[0]
            if h[0] & 0x80:                                   # last metadata block
                break
    return min(pos, size)


def _back(fh, size: int) -> int:
    """Where the audio ends: before an APEv2 tag and/or an ID3v1 tag."""
    end = size
    if end >= 128:
        fh.seek(end - 128)
        if fh.read(3) == b"TAG":
            end -= 128
    if end >= 32:
        fh.seek(end - 32)
        foot = fh.read(32)
        if foot[:8] == b"APETAGEX":
            _ver, tsize, _n, flags = struct.unpack("<4I", foot[8:24])
            start = end - tsize - (32 if flags & 0x80000000 else 0)
            if 0 <= start < end:
                end = start
    return end


def audio_region(path: str) -> tuple[int, int]:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        start = _front(fh, size)
        end = _back(fh, size)
    return (start, end) if end > start else (0, size)


def audio_identity(path: str) -> str:
    """One open, reads near the start and the end of the file only."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        start = _front(fh, size)
        end = _back(fh, size)
        if end <= start:
            start, end = 0, size
        n = end - start
        h = hashlib.sha256(str(n).encode())
        fh.seek(start)
        h.update(fh.read(min(EDGE, n)))
        fh.seek(max(start, end - EDGE))
        h.update(fh.read(min(EDGE, n)))
    return "e1:" + h.hexdigest()


def small_identity(path: str) -> str | None:
    if os.path.getsize(path) > SMALL:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return "s1:" + h.hexdigest()


def identity(path: str, kind: str, probe_ok: bool = True) -> str | None:
    """The identity the scan records for a file; None when there is none (unreadable audio,
    large non-audio files). Raises FileNotFoundError if the file vanished."""
    if kind == "audio":
        return audio_identity(path) if probe_ok else None
    return small_identity(path)
