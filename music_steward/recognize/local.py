"""A "Local" candidate: organize an album from what is already there, for albums no online
source knows (on the NAS APE sample, 17 of 40 had no real match anywhere).

Where each field comes from, in order:
  cue image album   the cue sheet (TITLE, PERFORMER, REM DATE, every track's TITLE/PERFORMER)
  files             their tags (garbled GBK/Big5 tags use the repair proposal),
                    then the file name ("03 - Title.mp3"), then the folder name ("Artist - Album (1998)")
The candidate is stored like any other (source 'Local', no distance), so Accept, Edit fields,
plan, apply and undo work unchanged. It never carries MusicBrainz IDs.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter

from .units import DISC_DIR

LOCAL = "Local"
YEAR = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)")
LEADING_NO = re.compile(r"^\s*(?:(?:cd|disc|disk)\s*\d+\s*[-_.]\s*)?(\d{1,3})\s*(?:[-_.、)]\s*|\s+)(?=\S)", re.I)


PLACEHOLDER = re.compile(r"^(未知(标题|標題|专辑|專輯|艺术家|藝術家|表演者|歌手)?|unknown( (artist|album|title|performer))?|"
                         r"untitled|no title|track ?\d*|音轨 ?\d*|音軌 ?\d*)$", re.I)
FORMAT_NOTE = re.compile(r"\s*[\[(（【]\s*(ape|flac|wav|dsd|cue|wv|tta|无损|無損|整轨|整軌|分轨|分軌|mp3|320k)"
                         r"(\s*[+&/\s]\s*(ape|flac|wav|dsd|cue|wv|tta|无损|無損|整轨|整軌|分轨|分軌|mp3|320k))*\s*[\])）】]", re.I)


def clean(s: str | None, number: int | None = None) -> str | None:
    """Tidy a name taken from a cue sheet, tag or folder: placeholders count as missing;
    download-site noise ('.-.', '.专辑', '(APE)') and a repeated track number are dropped."""
    if not s:
        return None
    s = FORMAT_NOTE.sub("", s.strip()).strip(" .")
    s = re.sub(r"\.-\.", " - ", s)
    s = re.sub(r"[.\s]*(专辑|專輯)$", "", s)
    # '林子祥.陈奕迅' / '].专辑' style separators between CJK or brackets (not 'Op.47', not 'Vol.2')
    s = re.sub(r"(?<=[\u4e00-\u9fff\]】])\.(?=[\u4e00-\u9fff\[【])", " ", s)
    m = re.fullmatch(r"(.*?)\s*[\[【]([^\]】]+)[\]】]", s)
    if m and (not m.group(1) or m.group(1).endswith("-")):
        s = (m.group(1) + " " + m.group(2)).strip()   # 'Artist - [Album]' -> 'Artist - Album'
    if number is not None:
        m = LEADING_NO.match(s)
        if m and int(m.group(1)) == number:
            s = s[m.end():]
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    return None if not s or PLACEHOLDER.match(s) else s


def _year(s) -> int | None:
    m = YEAR.search(str(s or ""))
    return int(m.group(1)) if m else None


def _int(s) -> int | None:
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def _majority(values, share=0.5):
    vals = [v for v in values if v]
    if not vals:
        return None
    v, n = Counter(vals).most_common(1)[0]
    return v if n / len(values) >= share else None


def _tags(f) -> dict:
    """File tags with the repair proposal applied to garbled fields."""
    t = json.loads(f["tags_json"] or "{}")
    for i in json.loads(f["tag_issues_json"] or "[]"):
        if i.get("proposal"):
            t[i["field"]] = i["proposal"]
    return {k: clean(v) if k in ("album", "albumartist", "artist", "title") and isinstance(v, str) else v
            for k, v in t.items()}


def album_folder(folder: str) -> str:
    """The album's own folder name: 'Box Set' for 'Box Set/CD 2'."""
    parts = [p for p in folder.split("/") if p]
    while len(parts) > 1 and DISC_DIR.match(parts[-1]):
        parts.pop()
    return parts[-1] if parts else ""


def parse_folder(name: str) -> dict:
    """'Artist - Album (1998)' / '[1998] Album' / 'Album' -> artist, album, year."""
    year = _year(name)
    s = clean(name) or ""
    s = re.sub(r"[\[(（【]\s*(19|20)\d{2}\s*[\])）】]", " ", s)
    s = re.sub(r"^\s*(19|20)\d{2}\s*[-_.]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -_")
    artist, album = None, s
    m = re.match(r"^(.+?)\s+[-–—－]\s+(.+)$", s)      # only a spaced dash: "Jay-Z", "A-ha" stay whole
    if m:
        artist, album = m.group(1).strip(), m.group(2).strip()
    return {"artist": artist, "album": album or None, "year": year}


def _title_from_name(relpath: str, artist: str | None) -> tuple[str, int | None]:
    stem = SUFFIX.sub("", os.path.splitext(relpath.rsplit("/", 1)[-1])[0])
    n = None
    m = LEADING_NO.match(stem)
    if m:
        n, stem = int(m.group(1)), stem[m.end():]
    if artist and stem.lower().startswith(artist.lower()):
        rest = stem[len(artist):].lstrip()
        if rest[:1] in "-–—_":
            stem = rest[1:].strip()
    return clean(stem) or "Untitled", n


SUFFIX = re.compile(r"\s*[(（\[【]\s*([^()（）\[\]【】]+?)\s+[-–—]\s+([^()（）\[\]【】]+?)\s*[)）\]】]\s*$")


def common_suffix(relpaths: list[str]) -> tuple[str, str] | None:
    """'01. 紫菱洲歌 (郑绪岚 – 情系红楼梦).flac' on every file -> ('郑绪岚', '情系红楼梦')."""
    found = {m.groups() if (m := SUFFIX.search(os.path.splitext(r.rsplit("/", 1)[-1])[0])) else None
             for r in relpaths}
    if len(found) == 1 and None not in found and len(relpaths) >= 2:
        a, b = found.pop()
        return clean(a), clean(b)
    return None


def _disc_of(relpath: str, unit_folder: str) -> int | None:
    folder = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
    leaf = folder.rsplit("/", 1)[-1]
    return _int(re.sub(r"\D", " ", leaf)) if DISC_DIR.match(leaf) else None


def from_cue(con: sqlite3.Connection, u) -> dict:
    from .. import cue as cuemod
    cf = con.execute("SELECT f.fs_relpath, r.path AS root FROM file f JOIN root r ON r.id=f.root_id WHERE f.id=?",
                     (u["cue_file_id"],)).fetchone()
    sheet = cuemod.parse(os.path.join(cf["root"], cf["fs_relpath"]))
    audio = con.execute("SELECT duration FROM file WHERE id=?", (json.loads(u["file_ids"])[0],)).fetchone()
    f = cuemod.with_lengths(max(sheet.files, key=lambda x: len(x.tracks)), audio["duration"])
    folder = parse_folder(album_folder(u["folder"]))
    s_title, s_perf = clean(sheet.title), clean(sheet.performer)
    album = s_title or folder["album"]
    artist = s_perf or folder["artist"]
    if album and artist and " - " in album:          # '群星 - 2009流行发烧唱片' -> '2009流行发烧唱片'
        left, right = album.split(" - ", 1)
        if artist.casefold() in left.casefold():
            album = right
    performers = {clean(t.performer) for t in f.tracks} - {None}
    if not artist and len(performers) == 1:
        artist = performers.pop()
    tracks = [{"cue_track": t.number, "cue_title": t.title,
               "title": clean(t.title, t.number) or f"Track {t.number}", "artist": clean(t.performer) or artist, "medium": 1, "medium_index": i + 1, "index": i + 1,
               "length": t.length} for i, t in enumerate(f.tracks)]
    origin = {"album": "cue sheet" if s_title else "folder name",
              "artist": "cue sheet" if s_perf else ("folder name" if folder["artist"] else None),
              "year": "cue sheet" if _year(sheet.date) else ("folder name" if folder["year"] else None),
              "titles": "cue sheet"}
    return {"album": album, "artist": artist, "year": _year(sheet.date) or folder["year"], "mediums": 1,
            "tracks": tracks, "origin": origin}


def from_files(con: sqlite3.Connection, u) -> dict:
    ids = json.loads(u["file_ids"])
    rows = con.execute(f"SELECT id, relpath, tags_json, tag_issues_json, duration FROM file WHERE id IN "
                       f"({','.join('?' * len(ids))}) ORDER BY relpath", ids).fetchall()
    tags = {f["id"]: _tags(f) for f in rows}
    folder = parse_folder(album_folder(u["folder"]))
    suffix = common_suffix([f["relpath"] for f in rows])
    if suffix:   # file names agree on '(Artist – Album)': better than the folder name
        folder = {"artist": suffix[0] or folder["artist"], "album": suffix[1] or folder["album"],
                  "year": folder["year"]}
    is_album = u["kind"] == "album"
    origin = {}
    album = artist = year = None
    if is_album:
        album = _majority([tags[f["id"]].get("album") for f in rows])
        origin["album"] = "tags" if album else ("file names" if suffix else "folder name")
        album = album or folder["album"]
        artist = _majority([tags[f["id"]].get("albumartist") for f in rows]) or \
            _majority([tags[f["id"]].get("artist") for f in rows])
        if artist:
            origin["artist"] = "tags"
        elif folder["artist"]:
            artist, origin["artist"] = folder["artist"], "file names" if suffix else "folder name"
        elif len({tags[f["id"]].get("artist") for f in rows if tags[f["id"]].get("artist")}) > 1:
            artist, origin["artist"] = "Various Artists", "tags (several artists)"
    else:
        t = tags[rows[0]["id"]]
        artist = t.get("artist") or t.get("albumartist")
        origin["artist"] = "tags" if artist else None
    year = _majority([_year(tags[f["id"]].get("date") or tags[f["id"]].get("year")) for f in rows])
    origin["year"] = "tags" if year else ("folder name" if is_album and folder["year"] else None)
    year = year or (folder["year"] if is_album else None)
    tracks, from_name = [], 0
    for pos, f in enumerate(rows):
        t = tags[f["id"]]
        name_title, name_no = _title_from_name(f["relpath"], t.get("artist") or artist)
        title = t.get("title")
        if not title:
            title, from_name = name_title, from_name + 1
        disc = _int(t.get("disc")) or _disc_of(f["relpath"], u["folder"]) or 1
        no = _int(t.get("track")) or name_no
        tracks.append({"file_id": f["id"], "title": title, "artist": t.get("artist") or artist,
                       "medium": disc, "medium_index": no, "length": f["duration"], "_pos": pos})
    # files without a usable number keep their order on the disc
    for disc in {t["medium"] for t in tracks}:
        on = [t for t in tracks if t["medium"] == disc]
        if any(t["medium_index"] is None for t in on) or len({t["medium_index"] for t in on}) < len(on):
            for i, t in enumerate(on):
                t["medium_index"] = i + 1
    for i, t in enumerate(tracks):
        t["index"] = i + 1
        t.pop("_pos")
    origin["titles"] = "tags" if not from_name else ("file names" if from_name == len(rows) else "tags + file names")
    return {"album": album, "artist": artist, "year": year,
            "mediums": max(t["medium"] for t in tracks) if is_album else 1, "tracks": tracks, "origin": origin}


def describe(con: sqlite3.Connection, unit_id: int) -> dict:
    u = con.execute("SELECT * FROM unit WHERE id=?", (unit_id,)).fetchone()
    return from_cue(con, u) if u["kind"] == "cue" else from_files(con, u)


def add(con: sqlite3.Connection, unit_id: int) -> int:
    """(Re)create the unit's Local candidate, ranked after every online one. Returns its id."""
    info = describe(con, unit_id)
    old = [r[0] for r in con.execute("SELECT id FROM candidate WHERE unit_id=? AND source=?", (unit_id, LOCAL))]
    rank = (con.execute("SELECT MAX(rank) FROM candidate WHERE unit_id=? AND COALESCE(source,'')<>?",
                        (unit_id, LOCAL)).fetchone()[0] or 0) + 1
    n = len(info["tracks"])
    cid = con.execute(
        "INSERT INTO candidate(unit_id, rank, source, album_id, album, artist, year, country, label, media, "
        "mediums, distance, penalties_json, extra_items_json, extra_tracks_json, fingerprint_agrees, n_mapped, "
        "origin_json) VALUES(?,?,?,NULL,?,?,?,NULL,NULL,NULL,?,NULL,'[]','[]','[]',0,?,?)",
        (unit_id, rank, LOCAL, info["album"], info["artist"], info["year"], info["mediums"], n,
         json.dumps(info["origin"], ensure_ascii=False))).lastrowid
    for t in info["tracks"]:
        if "cue_track" in t:
            con.execute("INSERT INTO cand_cue_track(candidate_id, cue_track, cue_title, track_index, medium, "
                        "medium_index, title, artist, length) VALUES(?,?,?,?,?,?,?,?,?)",
                        (cid, t["cue_track"], t["cue_title"], t["index"], t["medium"], t["medium_index"],
                         t["title"], t["artist"], t["length"]))
        else:
            con.execute("INSERT INTO cand_track(candidate_id, file_id, track_index, medium, medium_index, title, "
                        "artist, length) VALUES(?,?,?,?,?,?,?,?)",
                        (cid, t["file_id"], t["index"], t["medium"], t["medium_index"], t["title"],
                         t["artist"], t["length"]))
    for oid in old:   # an accepted Local candidate stays accepted (with its edits) when rebuilt
        con.execute("UPDATE decision SET candidate_id=? WHERE candidate_id=?", (cid, oid))
        con.execute("DELETE FROM candidate WHERE id=?", (oid,))
    return cid
