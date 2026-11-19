"""Whole-file and audio-stream hashes.

Audio hash methods (verified in M1 against tag edits):
  packet  -- ffmpeg stream copy into its hash muxer: fast, no decoding.
             Invariant under tag edits for MP3 (ID3v2, ID3v1, APEv2), FLAC, M4A, WAV.
  pcm     -- decode to PCM, then hash. Used for APE: ffmpeg's APE demuxer folds a
             trailing APEv2 tag into the last packet, so 'packet' changes when tags do.
"""
from __future__ import annotations

import hashlib
import os
import subprocess

PCM_EXTS = {".ape", ".wv", ".tta"}  # demuxers where trailing tags leak into packets
PCM_CODECS = {"ape", "wavpack", "tta"}


class HashError(Exception):
    pass


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audio_hash_method(ext: str, codec: str | None = None) -> str:
    """Decided by the real codec when known (APE named .mp3 exists), else the extension."""
    if codec:
        return "pcm" if codec in PCM_CODECS else "packet"
    return "pcm" if ext.lower() in PCM_EXTS else "packet"


def audio_hash(path: str, ext: str, timeout: float = 600, codec: str | None = None) -> tuple[str, str]:
    method = audio_hash_method(ext, codec)
    from .probe import audio_offset
    off = audio_offset(path, ext)
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if off:
        cmd += ["-skip_initial_bytes", str(off)]
    cmd += ["-i", path, "-map", "0:a:0"]
    if method == "packet":
        cmd += ["-c", "copy"]
    cmd += ["-f", "hash", "-hash", "sha256", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = r.stdout.decode(errors="replace").strip()
    if r.returncode != 0 or not out.startswith("SHA256="):
        raise HashError((r.stderr.decode(errors="replace").strip() or out or "ffmpeg failed")[:500])
    return out.split("=", 1)[1], method


def copy_audio_fingerprint(path: str, ext: str, codec: str | None) -> str:
    """Fingerprint used by apply to prove that writing tags left the audio untouched.

    APE / WavPack / TTA: SHA-256 of the exact audio BYTES -- everything between an ID3 block in
    front and the APEv2 / ID3v1 tags at the end, which is all a tag write may change. Exact and
    ~40x faster than decoding (a 344 MB APE: 0.3 s instead of 12.5 s, twice per file).
    Other formats: the FFmpeg packet hash (tags live in separate blocks there)."""
    if codec not in PCM_CODECS:
        return "packet:" + audio_hash(path, ext, codec=codec)[0]
    from .probe import audio_offset
    from .tags import apev2_span
    start = audio_offset(path, ext) or 0
    end = os.path.getsize(path)
    with open(path, "rb") as fh:
        if end >= 128:
            fh.seek(end - 128)
            if fh.read(3) == b"TAG":
                end -= 128
    span = apev2_span(path)
    if span and span[1] == end:
        end = span[0]
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        fh.seek(start)
        left = end - start
        while left > 0:
            buf = fh.read(min(1 << 20, left))
            if not buf:
                break
            h.update(buf)
            left -= len(buf)
    return "bytes:" + h.hexdigest()
