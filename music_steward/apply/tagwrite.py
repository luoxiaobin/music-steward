"""Write tags to a COPY (never to a source). Minimal edits: only the given fields change;
everything else (artwork, lyrics, ReplayGain, custom tags) is kept. Verified in M0 for
MP3 / FLAC / APE. MP3 is saved as ID3v2.3 for the widest player support."""
from __future__ import annotations

import mutagen

# app field -> per-format key
ID3_EASY = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist",
            "track": "tracknumber", "disc": "discnumber", "date": "date",
            "mb_trackid": "musicbrainz_trackid", "mb_albumid": "musicbrainz_albumid"}
VORBIS = {"title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "albumartist": "ALBUMARTIST",
          "track": "TRACKNUMBER", "disc": "DISCNUMBER", "date": "DATE",
          "mb_trackid": "MUSICBRAINZ_TRACKID", "mb_albumid": "MUSICBRAINZ_ALBUMID"}
APE = {"title": "Title", "artist": "Artist", "album": "Album", "albumartist": "Album Artist",
       "track": "Track", "disc": "Disc", "date": "Year",
       "mb_trackid": "MUSICBRAINZ_TRACKID", "mb_albumid": "MUSICBRAINZ_ALBUMID"}


def write_tags(path: str, fields: dict, codec: str):
    fields = {k: str(v) for k, v in fields.items() if v not in (None, "")}
    if not fields:
        return
    if codec == "ape":
        from mutagen.apev2 import APEv2, APENoHeaderError
        try:
            t = APEv2(path)
        except APENoHeaderError:
            t = APEv2()
        except Exception:  # noqa: BLE001 -- APEBadItemError etc.: a corrupt or GBK-encoded tag
            t = _rebuild_ape(path)
        for k, v in fields.items():
            # APEv2 keys are case-insensitive: replace whatever spelling exists
            for existing in [e for e in t.keys() if e.lower() == APE[k].lower()]:
                del t[existing]
            t[APE[k]] = v
        t.save(path)
        return
    if codec in ("mp3", "mp2"):
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3NoHeaderError
        try:
            t = EasyID3(path)
        except ID3NoHeaderError:
            t = EasyID3()
        for k, v in fields.items():
            t[ID3_EASY[k]] = [v]
        t.save(path, v2_version=3)
        return
    f = mutagen.File(path)
    if f is None:
        raise ValueError(f"cannot write tags to {path}")
    if f.tags is None:
        f.add_tags()
    if codec in ("aac", "alac"):
        from mutagen.easymp4 import EasyMP4
        f = EasyMP4(path)
        for k, v in fields.items():
            key = {"track": "tracknumber", "disc": "discnumber"}.get(k, k)
            if key in ("mb_trackid", "mb_albumid"):
                key = {"mb_trackid": "musicbrainz_trackid", "mb_albumid": "musicbrainz_albumid"}[key]
            f[key] = [v]
        f.save()
        return
    for k, v in fields.items():   # FLAC / Ogg: Vorbis comments
        f[VORBIS[k]] = [v]
    f.save()


def _rebuild_ape(path: str):
    """The COPY's APEv2 tag cannot be loaded (GBK bytes where UTF-8 is required, or items with
    wrong lengths written by a buggy tagger -- seen on the NAS). Salvage every readable text
    item (UTF-8 as is; GBK/Big5 converted through the usual repair proposal), drop the corrupt
    ones, remove the old tag and return a clean one to be filled and saved."""
    from mutagen.apev2 import APEv2, is_valid_apev2_key
    from ..tags import _raw_apev2, apev2_span, repair_latin1
    t = APEv2()
    for key, raw in _raw_apev2(path) or []:
        if not is_valid_apev2_key(key) or any(e.lower() == key.lower() for e in t.keys()):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            prop = repair_latin1(raw.decode("latin-1"))
            if not prop:
                continue
            text = prop["proposal"]
        if text.strip():
            t[key] = text
    span = apev2_span(path)
    if span:   # cut the old tag out (mutagen's delete() would parse the broken items again)
        start, end = span
        with open(path, "r+b") as fh:
            fh.seek(end)
            after = fh.read()            # an ID3v1 tag, if any
            fh.seek(start)
            fh.write(after)
            fh.truncate()
    return t
