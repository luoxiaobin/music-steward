"""Read tags (never write) and detect legacy-encoding mojibake.

Garbled Chinese usually comes from GBK/GB18030 or Big5 bytes stored in an ID3
frame marked Latin-1 (or in ID3v1, which is always Latin-1). We re-encode such
text to its original bytes, try GB18030 and Big5, and propose the best reading.
Nothing is changed in the file; the proposal is for review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import mutagen

FIELDS = ("title", "artist", "album", "albumartist", "track", "disc", "date", "genre")

ID3_MAP = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TPE2": "albumartist",
           "TRCK": "track", "TPOS": "disc", "TDRC": "date", "TYER": "date", "TCON": "genre"}
VORBIS_MAP = {"title": "title", "artist": "artist", "album": "album",
              "albumartist": "albumartist", "album artist": "albumartist",
              "tracknumber": "track", "discnumber": "disc", "date": "date", "year": "date",
              "genre": "genre"}
MP4_MAP = {"\xa9nam": "title", "\xa9ART": "artist", "\xa9alb": "album", "aART": "albumartist",
           "trkn": "track", "disk": "disc", "\xa9day": "date", "\xa9gen": "genre"}


@dataclass
class TagInfo:
    tag_format: str | None
    values: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)   # each: {field, kind, shown_as, proposal, readings}


def read_tags(path: str) -> TagInfo:
    try:
        return _read_tags(path)
    except Exception as e:  # noqa: BLE001 -- mutagen raises many types
        ti = _tolerant(path)
        if ti is None:
            raise
        ti.issues.append({"kind": "tag_read_fallback", "error": f"{type(e).__name__}: {e}"[:200]})
        return ti


def _tolerant(path: str) -> TagInfo | None:
    """When mutagen refuses a file: an APE file with an ID3 block in front (mutagen takes it
    for MP3), or an APEv2 tag holding GBK/Big5 bytes where UTF-8 is required. Read what is
    there; legacy text gets the usual repair proposal."""
    from mutagen.id3 import ID3
    with open(path, "rb") as fh:
        head = fh.read(3)
    if head == b"ID3":
        try:
            ti = TagInfo("ID3 (prefix)")
            _read_id3(ID3(path), ti)
            if ti.values:
                return ti
        except Exception:  # noqa: BLE001
            pass
    items = _raw_apev2(path)
    if items is None:
        return None
    ti = TagInfo("APEv2 (lenient)")
    for key, raw in items:
        fld = VORBIS_MAP.get(key.lower())
        if not fld or fld in ti.values:
            continue
        try:
            ti.values[fld] = raw.decode("utf-8").strip("\x00").strip()
            continue
        except UnicodeDecodeError:
            pass
        shown = raw.decode("latin-1").strip("\x00").strip()
        ti.values[fld] = shown
        prop = repair_latin1(shown)
        if prop:
            ti.issues.append({"field": fld, "kind": "legacy_encoding", "frame": f"APE:{key}",
                              "shown_as": shown, **prop})
    for k in [k for k, v in ti.values.items() if not v]:
        del ti.values[k]
    return ti


def _raw_apev2(path: str) -> list[tuple[str, bytes]] | None:
    """Minimal APEv2 reader: footer 'APETAGEX' at the end (or before a 128-byte ID3v1 tag)."""
    import struct
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        for footer_at in (end - 32, end - 128 - 32):
            if footer_at < 0:
                continue
            fh.seek(footer_at)
            foot = fh.read(32)
            if foot[:8] == b"APETAGEX":
                break
        else:
            return None
        _ver, size, count, _flags = struct.unpack("<4I", foot[8:24])
        if size < 32 or size > 16 * 1024 * 1024 or count > 1000 or footer_at - (size - 32) < 0:
            return None
        fh.seek(footer_at - (size - 32))
        body = fh.read(size - 32)
    out, pos = [], 0
    for _ in range(count):
        if pos + 8 > len(body):
            break
        vlen, iflags = struct.unpack("<2I", body[pos:pos + 8])
        k_end = body.find(b"\x00", pos + 8)
        if k_end < 0 or k_end + 1 + vlen > len(body):
            break
        key = body[pos + 8:k_end].decode("latin-1")
        value = body[k_end + 1:k_end + 1 + vlen]
        pos = k_end + 1 + vlen
        if (iflags >> 1) & 3 == 0:          # text item (not binary / external)
            out.append((key, value.split(b"\x00")[0]))
    return out


def _read_tags(path: str) -> TagInfo:
    f = mutagen.File(path)
    if f is None:
        return TagInfo(None)
    tags = getattr(f, "tags", None)
    if tags is None:
        return TagInfo(type(f).__name__ + ":none")
    name = type(tags).__name__
    ti = TagInfo(name)
    if name in ("ID3", "_WaveID3", "ID3FileType"):
        _read_id3(tags, ti)
    elif name == "VCFLACDict" or name.startswith("VComment") or name == "OggVCommentDict":
        for k, v in tags.items():
            fld = VORBIS_MAP.get(k.lower())
            if fld and v and fld not in ti.values:
                ti.values[fld] = str(v[0])
                _check_replacement(ti, fld, str(v[0]))
    elif name == "MP4Tags":
        for k, fld in MP4_MAP.items():
            v = tags.get(k)
            if v:
                x = v[0]
                ti.values[fld] = f"{x[0]}/{x[1]}" if isinstance(x, tuple) else str(x)
    elif name == "APEv2":
        for k, v in tags.items():
            fld = VORBIS_MAP.get(k.lower())
            if fld and fld not in ti.values and hasattr(v, "__str__"):
                s = str(v)
                ti.values[fld] = s
                _check_replacement(ti, fld, s)
    else:
        for k, v in tags.items():
            fld = VORBIS_MAP.get(str(k).lower())
            if fld and fld not in ti.values:
                ti.values[fld] = str(v[0] if isinstance(v, list) else v)
    for k in list(ti.values):
        ti.values[k] = ti.values[k].strip("\x00").strip() or None
        if ti.values[k] is None:
            del ti.values[k]
    return ti


def _read_id3(tags, ti: TagInfo):
    for fid, fld in ID3_MAP.items():
        fr = tags.get(fid)
        if not fr or not getattr(fr, "text", None) or fld in ti.values:
            continue
        text = str(fr.text[0])
        ti.values[fld] = text
        if getattr(fr, "encoding", None) == 0 and any(0x80 <= ord(c) <= 0xFF for c in text):
            prop = repair_latin1(text)
            if prop:
                ti.issues.append({"field": fld, "kind": "legacy_encoding", "frame": fid,
                                  "shown_as": text, **prop})


def _check_replacement(ti: TagInfo, fld: str, s: str):
    if "�" in s:
        ti.issues.append({"field": fld, "kind": "invalid_utf8", "shown_as": s, "proposal": None,
                          "readings": {}})


def _cjk_ratio(s: str) -> float:
    hi = [c for c in s if ord(c) > 0x7F]
    if not hi:
        return 0.0
    cjk = sum(1 for c in hi if 0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF
              or 0x3000 <= ord(c) <= 0x303F or 0xFF00 <= ord(c) <= 0xFFEF)
    return cjk / len(hi)


def _common_ratio(s: str) -> float:
    """Share of CJK chars that exist in GB2312 or Big5 -- i.e. ordinary characters.
    Mis-decodings tend to land on rare characters outside both sets."""
    cjk = [c for c in s if ord(c) > 0x7F]
    if not cjk:
        return 0.0
    ok = 0
    for c in cjk:
        for enc in ("gb2312", "big5"):
            try:
                c.encode(enc)
                ok += 1
                break
            except UnicodeEncodeError:
                pass
    return ok / len(cjk)


def repair_latin1(text: str) -> dict | None:
    """Return {'proposal', 'encoding', 'readings'} or None if it looks like real Latin-1."""
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return None
    # GBK/Big5 characters are byte PAIRS; a lone high byte between ASCII letters
    # is a real accented Latin letter ("Motörhead", "Sigur Rós") -- leave it alone.
    runs, cur = [], 0
    for b in raw:
        if b >= 0x80:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    hi = sum(runs)
    if not hi:
        return None
    paired = sum(r for r in runs if r >= 2) / hi >= 0.8
    readings = {}
    for enc in ("gb18030", "big5"):
        try:
            readings[enc] = raw.decode(enc)
        except UnicodeDecodeError:
            pass
    scored = []
    for enc, s in readings.items():
        cjk = _cjk_ratio(s)
        # Big5/GBK trail bytes may be ASCII (德 = BC 77 'w'), so unpaired high bytes
        # are accepted only when the result is mostly CJK overall -- "Motörhead"
        # would decode to 1 CJK char among 9 and is rejected.
        visible = [ch for ch in s if not ch.isspace()]
        cjk_overall = sum(1 for ch in visible if ord(ch) > 0x2E80) / max(1, len(visible))
        if cjk >= 0.8 and (paired or cjk_overall >= 0.5):
            scored.append((cjk + _common_ratio(s), enc == "gb18030", enc, s))
    if not scored:
        return None
    scored.sort(reverse=True)
    _, _, enc, s = scored[0]
    return {"proposal": s, "encoding": enc, "readings": readings}
