"""Technical properties via ffprobe (container and codec kept separate)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict


class ProbeError(Exception):
    pass


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


def probe(path: str, timeout: float = 60) -> Props:
    r = subprocess.run(
        ["ffprobe", "-v", "warning", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise ProbeError((r.stderr.decode(errors="replace").strip() or f"ffprobe exit {r.returncode}")[:500])
    try:
        info = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"unparseable ffprobe output: {e}")
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise ProbeError("no audio stream")
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
    )
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
