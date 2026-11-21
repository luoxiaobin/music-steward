"""Cue sheets: parse (with encoding detection) and rewrite (keeping the encoding).

On the NAS most cue sheets are GBK/GB18030, not UTF-8 (M0 finding). A cue describes
an "image" album: one long audio file (usually APE) plus the start of every track.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from charset_normalizer import from_bytes

_Q = r'"([^"]*)"|(\S+)'


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw, cp_isolation=["gb18030", "big5", "shift_jis", "cp1252"]).best()
    if best:
        return best.encoding
    # the detector can give up on short sheets (seen on a rewritten GB18030 cue): most cue
    # sheets here are GBK/Big5, so try those strictly before falling back to Latin-1
    for enc in ("gb18030", "big5"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    return "latin-1"


@dataclass
class CueTrack:
    number: int
    title: str | None = None
    performer: str | None = None
    start: float = 0.0          # seconds into the file (INDEX 01)
    length: float | None = None  # filled once the file's duration is known


@dataclass
class CueFile:
    name: str
    tracks: list[CueTrack] = field(default_factory=list)


@dataclass
class Cue:
    encoding: str
    title: str | None = None
    performer: str | None = None
    date: str | None = None
    files: list[CueFile] = field(default_factory=list)

    @property
    def tracks(self) -> list[CueTrack]:
        return [t for f in self.files for t in f.tracks]


def _arg(rest: str) -> str:
    m = re.match(_Q, rest.strip())
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else rest.strip()


def _time(mmssff: str) -> float:
    m, s, f = (int(x) for x in mmssff.split(":"))
    return m * 60 + s + f / 75.0


def parse_bytes(raw: bytes) -> Cue:
    enc = detect_encoding(raw)
    cue = Cue(encoding=enc)
    cur_file: CueFile | None = None
    cur: CueTrack | None = None
    for line in raw.decode(enc, errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        cmd, rest = parts[0].upper(), (parts[1] if len(parts) > 1 else "")
        if cmd == "FILE":
            m = re.match(r'\s*(?:"([^"]*)"|(\S+))', rest)
            cur_file = CueFile(name=(m.group(1) if m.group(1) is not None else m.group(2)) if m else rest)
            cue.files.append(cur_file)
            cur = None
        elif cmd == "TRACK" and cur_file is not None:
            n = int(re.match(r"\d+", rest).group(0)) if re.match(r"\d+", rest) else len(cur_file.tracks) + 1
            cur = CueTrack(number=n)
            cur_file.tracks.append(cur)
        elif cmd == "TITLE":
            (setattr(cur, "title", _arg(rest)) if cur else setattr(cue, "title", _arg(rest)))
        elif cmd == "PERFORMER":
            (setattr(cur, "performer", _arg(rest)) if cur else setattr(cue, "performer", _arg(rest)))
        elif cmd == "REM" and rest.upper().startswith("DATE") and cur is None:
            cue.date = rest.split(None, 1)[1].strip() if len(rest.split(None, 1)) > 1 else None
        elif cmd == "INDEX" and cur is not None:
            bits = rest.split()
            if len(bits) == 2 and bits[0] == "01":
                try:
                    cur.start = _time(bits[1])
                except ValueError:
                    pass
    return cue


def parse(path: str) -> Cue:
    with open(path, "rb") as fh:
        return parse_bytes(fh.read())


def file_for(sheet: Cue, audio_name: str) -> CueFile:
    """The FILE entry of `sheet` that describes the audio file `audio_name` (a base name).

    A 2-CD image cue has one FILE per disc image; each image is its own album unit, so every
    consumer must take that image's own tracks -- not the FILE with the most tracks, which gave
    disc 2 the track list of disc 1. Same matching as the unit grouping: exact name, then the
    same stem (cues often say .wav for an .ape), then -- one image only -- that one."""
    import os
    import unicodedata
    want = unicodedata.normalize("NFC", audio_name).casefold()
    stem = os.path.splitext(want)[0]
    names = [(unicodedata.normalize("NFC", f.name).casefold(), f) for f in sheet.files]
    for n, f in names:
        if os.path.basename(n.replace("\\", "/")) == want:
            return f
    for n, f in names:
        if os.path.splitext(os.path.basename(n.replace("\\", "/")))[0] == stem:
            return f
    return max(sheet.files, key=lambda x: len(x.tracks))


def with_lengths(f: CueFile, total: float | None) -> CueFile:
    """Track lengths from consecutive INDEX 01 starts; the last runs to the file's end."""
    for i, t in enumerate(f.tracks):
        nxt = f.tracks[i + 1].start if i + 1 < len(f.tracks) else total
        t.length = max(0.0, nxt - t.start) if nxt is not None else None
    return f


def rewrite_bytes(raw: bytes, file_renames: dict[str, str] | None = None, album: dict | None = None,
                  tracks: dict[int, dict] | None = None, out_encoding: str | None = None,
                  keep_file: str | None = None) -> tuple[bytes, str]:
    """Return the cue with FILE references renamed and (optionally) album/track TITLE and
    PERFORMER replaced. Everything else is kept. Written in `out_encoding` (e.g. "utf-8-sig":
    UTF-8 with a BOM, which macOS players need -- foobar2000 for Mac showed GB18030 cues as
    mojibake), or in the cue's ORIGINAL encoding when None.

    keep_file: the FILE name (as written in the cue) to keep; the other FILE sections are dropped
    and the kept tracks numbered from 1 -- one disc of a multi-image cue becomes a standalone cue
    for that disc's image. `tracks` stays keyed by the ORIGINAL track numbers."""
    enc = detect_encoding(raw)
    file_renames, album, tracks = file_renames or {}, album or {}, tracks or {}
    out, cur_track = [], None
    skipping, renumber = False, 0
    seen_album = {"TITLE": False, "PERFORMER": False}
    lines = raw.decode(enc).splitlines(keepends=True)
    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        eol = line[len(line.rstrip("\r\n")):]
        parts = stripped.split(None, 1)
        cmd = parts[0].upper() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if cmd == "FILE":
            m = re.match(r'\s*(?:"([^"]*)"|(\S+))(.*)$', rest)
            if m:
                name = m.group(1) if m.group(1) is not None else m.group(2)
                skipping = keep_file is not None and name != keep_file
                line = f'{indent}FILE "{file_renames.get(name, name)}"{m.group(3)}{eol}'
            cur_track = None
        elif skipping:
            continue
        elif cmd == "TRACK":
            n = re.match(r"\d+", rest)
            cur_track = int(n.group(0)) if n else None
            if keep_file is not None and n:
                renumber += 1
                line = f"{indent}TRACK {renumber:02d}{rest[n.end():]}{eol}"
        if skipping:
            continue
        elif cmd in ("TITLE", "PERFORMER"):
            key = cmd.lower()
            if cur_track is None and album.get(key):
                line = f'{indent}{cmd} "{album[key]}"{eol}'
                seen_album[cmd] = True
            elif cur_track is not None and tracks.get(cur_track, {}).get(key):
                line = f'{indent}{cmd} "{tracks[cur_track][key]}"{eol}'
        out.append(line)
    text = "".join(out)
    if out_encoding:
        return text.encode(out_encoding), out_encoding
    try:
        return text.encode(enc), enc
    except UnicodeEncodeError:
        # the new text (e.g. Traditional characters) does not fit the old legacy encoding
        return ("﻿" + text).encode("utf-8") if not enc.startswith("utf") else text.encode("utf-8"), "utf-8-sig"
