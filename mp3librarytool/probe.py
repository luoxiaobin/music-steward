"""Technical properties via ffprobe (container and codec kept separate)."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict


class ProbeError(Exception):
    def __init__(self, msg, note=None):
        super().__init__(msg)
        self.note = note


@dataclass
class Props:
    container: str | None
    codec: str | None
    duration: float | None
    duration_estimated: bool
    bit_rate: int | None
    bit_rate_estimated: bool
    sample_rate: int | None
    channels: int | None
    bits_per_sample: int | None
    audio_offset: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


APE_MAGIC = b"MAC "
PREFIX_SEARCH = 8 << 20  # some taggers prepend ID3v2 (with artwork) before the APE header


def audio_offset(path: str, ext: str) -> int:
    """Bytes to skip before the real audio header. Non-zero only for APE files with a
    tag (usually ID3v2) stuck on the front, which ffmpeg's APE demuxer rejects."""
    if ext.lower() != ".ape":
        return 0
    with open(path, "rb") as fh:
        head = fh.read(4)
        if head == APE_MAGIC or head[:3] != b"ID3":
            return 0
        fh.seek(0)
        i = fh.read(PREFIX_SEARCH).find(APE_MAGIC)
    return max(i, 0)


def sniff(path: str) -> str | None:
    """Why is this 'audio' file unreadable? Looks at content, never the extension."""
    try:
        with open(path, "rb") as fh:
            b = fh.read(4 << 20)
    except OSError as e:
        return f"cannot open: {e}"
    if not b:
        return "empty file"
    if not any(b):
        return "starts with at least 4 MB of zeros (damaged or incomplete copy)"
    if b"\x00\x00\x01\xba" in b:
        return "MPEG program stream (e.g. VCD video), not an audio file"
    if b[:4] == b"RIFF" and b[8:12] == b"CDXA":
        return "VCD RIFF/CDXA video, not an audio file"
    if b[:3] == b"ID3":
        return "ID3 tag followed by unrecognised data"
    return f"unrecognised data (starts {b[:4].hex()})"


def _ffprobe(path: str, timeout: float, offset: int = 0):
    cmd = ["ffprobe", "-v", "warning", "-print_format", "json", "-show_format", "-show_streams"]
    if offset:
        cmd += ["-skip_initial_bytes", str(offset)]
    return subprocess.run(cmd + [path], capture_output=True, timeout=timeout)


def probe(path: str, timeout: float = 60, ext: str | None = None) -> Props:
    ext = (ext or os.path.splitext(path)[1]).lower()
    offset = 0
    r = _ffprobe(path, timeout)
    if r.returncode != 0 and (offset := audio_offset(path, ext)):
        r = _ffprobe(path, timeout, offset)
    if r.returncode != 0:
        msg = (r.stderr.decode(errors="replace").strip() or f"ffprobe exit {r.returncode}")[-400:]
        raise ProbeError(msg, sniff(path))
    try:
        info = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"unparseable ffprobe output: {e}")
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise ProbeError("no audio stream", sniff(path))
    s, fmt = audio[0], info.get("format", {})
    warnings = r.stderr.decode(errors="replace")
    # ffprobe prints this when it had to guess duration from the bitrate
    duration_estimated = "Estimating duration from bitrate" in warnings
    bit_rate = _int(s.get("bit_rate")) or _int(fmt.get("bit_rate"))
    bits = _int(s.get("bits_per_raw_sample")) or _int(s.get("bits_per_sample")) or None
    p = Props(
        container=fmt.get("format_name"),
        codec=s.get("codec_name"),
        duration=_float(s.get("duration")) or _float(fmt.get("duration")),
        duration_estimated=duration_estimated,
        bit_rate=bit_rate,
        bit_rate_estimated=False,
        sample_rate=_int(s.get("sample_rate")),
        channels=_int(s.get("channels")),
        bits_per_sample=bits,
        audio_offset=offset,
    )
    if not p.channels or not p.sample_rate:
        # ffprobe "detects" e.g. a zero-filled file as MP3 with 0 channels; that is not audio
        raise ProbeError("no decodable audio (no channels / sample rate)", sniff(path))
    if p.codec == "mp3":
        p.bit_rate_estimated = _mp3_bitrate_is_estimate(path)
        p.duration_estimated = p.duration_estimated or p.bit_rate_estimated
    return p


def _mp3_bitrate_is_estimate(path: str) -> bool:
    """True when the MP3 has no Xing/Info/VBRI header: bitrate and duration
    are then extrapolated from the first frames and are wrong for VBR."""
    try:
        from mutagen.mp3 import MP3, BitrateMode
        info = MP3(path).info
        return info.bitrate_mode == BitrateMode.UNKNOWN
    except Exception:
        return True
