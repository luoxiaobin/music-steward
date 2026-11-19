"""Whole-file and audio-stream hashes.

Audio hash methods (verified in M1 against tag edits):
  packet  -- ffmpeg stream copy into its hash muxer: fast, no decoding.
             Invariant under tag edits for MP3 (ID3v2, ID3v1, APEv2), FLAC, M4A, WAV.
  pcm     -- decode to PCM, then hash. Used for APE: ffmpeg's APE demuxer folds a
             trailing APEv2 tag into the last packet, so 'packet' changes when tags do.
"""
from __future__ import annotations

import hashlib
import subprocess

PCM_EXTS = {".ape", ".wv", ".tta"}  # demuxers where trailing tags leak into packets


class HashError(Exception):
    pass


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audio_hash_method(ext: str) -> str:
    return "pcm" if ext.lower() in PCM_EXTS else "packet"


def audio_hash(path: str, ext: str, timeout: float = 600) -> tuple[str, str]:
    method = audio_hash_method(ext)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0"]
    if method == "packet":
        cmd += ["-c", "copy"]
    cmd += ["-f", "hash", "-hash", "sha256", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = r.stdout.decode(errors="replace").strip()
    if r.returncode != 0 or not out.startswith("SHA256="):
        raise HashError((r.stderr.decode(errors="replace").strip() or out or "ffmpeg failed")[:500])
    return out.split("=", 1)[1], method
