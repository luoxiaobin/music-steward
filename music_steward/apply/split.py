"""Cut one track out of a lossless cue-sheet image into a FLAC file. Reads the image, never writes it.

Exact to the sample: cue positions are frames of 1/75 s, so a track starts at a whole sample at
44.1 kHz and the other usual rates. FFmpeg seeks to a second before the track (fast on the NAS: no
need to decode the whole image up to the track) and keeps the original timestamps (-copyts); the
atrim filter then cuts at the exact sample positions. Checked: the tracks joined again equal the
image's audio sample for sample (tests/test_split.py).

The source's sample format is kept (16-bit stays 16-bit): splitting is lossless and changes nothing
but the container. verify() compares the decoded samples of the track with the same samples of the
image.
"""
from __future__ import annotations

import subprocess

from ..probe import audio_offset


class SplitError(Exception):
    pass


def _input(src: str, src_ext: str, seek: float) -> list[str]:
    args = ["-ss", f"{seek:.6f}", "-copyts"]
    off = audio_offset(src, src_ext)                 # APE with an ID3 tag in front (seen on the NAS)
    if off:
        args += ["-skip_initial_bytes", str(off)]
    return args + ["-i", src]


def _trim(start: float, end: float | None, rate: int) -> str:
    s = round(start * rate)
    f = f"atrim=start_pts={s}"
    if end is not None:
        f += f":end_pts={round(end * rate)}"
    return f + ",asetpts=PTS-STARTPTS"


def _run(cmd: list[str], timeout: float = 1800) -> str:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error"] + cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise SplitError((r.stderr.decode(errors="replace").strip() or "ffmpeg failed")[:500])
    return r.stdout.decode(errors="replace").strip()


def _format(bits: int | None) -> list[str]:
    if bits and bits <= 16:
        return ["-sample_fmt", "s16"]
    return ["-sample_fmt", "s32", "-bits_per_raw_sample", str(bits or 24)]


def extract(src: str, out: str, start: float, end: float | None, rate: int, bits: int | None,
            src_ext: str) -> None:
    """Write the track [start, end) of the image `src` to `out` (FLAC; `out` may already exist and is
    overwritten -- the caller created it exclusively)."""
    _run(["-y", *_input(src, src_ext, max(0.0, start - 1.0)), "-map", "0:a:0", "-af", _trim(start, end, rate),
          "-c:a", "flac", *_format(bits), "-f", "flac", out])


def _pcm_hash(args: list[str], bits: int | None) -> str:
    codec = "pcm_s16le" if bits and bits <= 16 else "pcm_s32le"
    out = _run([*args, "-map", "0:a:0", "-c:a", codec, "-f", "hash", "-hash", "sha256", "-"])
    if not out.startswith("SHA256="):
        raise SplitError(out[:200] or "no hash")
    return out.split("=", 1)[1]


def verify(src: str, track: str, start: float, end: float | None, rate: int, bits: int | None,
           src_ext: str) -> bool:
    """The track's decoded samples equal the image's samples [start, end)."""
    want = _pcm_hash([*_input(src, src_ext, max(0.0, start - 1.0)), "-af", _trim(start, end, rate)], bits)
    got = _pcm_hash(["-i", track], bits)
    return want == got
