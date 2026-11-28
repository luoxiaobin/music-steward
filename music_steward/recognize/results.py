"""Read recognition results back, with the app's own confidence rule.

Confidence (M0 evidence, to be tuned): beets' 'strong' (< 0.04) marked nothing on the
real collection, while correct releases sat at 0.09-0.30 with most tracks
fingerprint-linked to them.
  ready         distance < 0.15 with >= 60% of fingerprints agreeing, OR
                distance < 0.25 with >= 90% agreeing (titles may differ in punctuation;
                Phil Collins: 25/25 fingerprints, distance 0.17)
  needs_review  any other candidate
  no_match      no candidates, or top distance > 0.5 with no fingerprint agreement
"""
from __future__ import annotations

import json
import sqlite3

READY_DISTANCE = 0.15
READY_FP_SHARE = 0.6
READY_DISTANCE_STRONG_FP = 0.25
STRONG_FP_SHARE = 0.9
NO_MATCH_DISTANCE = 0.5


def release_key(con: sqlite3.Connection, candidate_id: int) -> str | None:
    """Stable identity of a candidate across re-matches: the MusicBrainz release for an
    album, the recording for a single track."""
    c = con.execute("SELECT album_id, source FROM candidate WHERE id=?", (candidate_id,)).fetchone()
    if c is None:
        return None
    if c["source"] == "Local":
        return "local"                  # rebuilt from the files' own info after a re-match
    if c["album_id"]:
        return f"release:{c['album_id']}"
    t = con.execute("SELECT recording_id FROM cand_track WHERE candidate_id=? LIMIT 1", (candidate_id,)).fetchone()
    return f"recording:{t['recording_id']}" if t and t["recording_id"] else None


def candidate_for(con: sqlite3.Connection, unit_id: int, key: str) -> int | None:
    for (cid,) in con.execute("SELECT id FROM candidate WHERE unit_id=? ORDER BY rank", (unit_id,)):
        if release_key(con, cid) == key:
            return cid
    return None


CUE_FP_TRACKS = 4        # cue albums: only this many tracks are fingerprinted (speed)
LIKELY_DISTANCE = 0.15   # no fingerprints needed: very close, every track mapped, none missing
TITLE_AGREES = 0.8       # similarity of normalised album titles for "weak: title agrees"


def _norm_title(t: str | None) -> str:
    import re
    from ..chinese import key
    t = key(t or "").casefold()
    t = re.sub(r"[\s\-_.·•:：,，/\\|()（）\[\]【】《》<>'\"“”‘’!！?？~～]+", "", t)
    return re.sub(r"(cd|disc|disk)\d+$", "", t)


def title_agrees(a: str | None, b: str | None) -> bool:
    from difflib import SequenceMatcher
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return False
    return x in y or y in x or SequenceMatcher(None, x, y).ratio() >= TITLE_AGREES


# Is this candidate the same album? The first trial on a real collection (2026-11-28) accepted 8 of 19 albums under
# another album's name -- a Cantonese opera as "A domani" by an artist called Ape, Cultural Revolution songs as a
# Japanese enka album -- and kept them through a second review, because the screen said nothing about the ARTIST and
# little about the title. These checks compare the candidate with what the album itself says: its folder names, its
# label and its files' artist tags (repaired when garbled), in one script (Simplified) and without accents.
VARIOUS = {"variousartists", "various", "va", "群星", "众星", "合辑", "群星合辑", "不同艺术家", "multipleartists",
           "群星演唱", "大合唱", "多位艺人"}
_NAME_SPLIT = None
_NUMBERS = None


def _plain(s: str | None) -> str:
    """Comparison form: Simplified, no accents, casefolded, no spaces or punctuation."""
    import re
    import unicodedata
    from ..chinese import key
    s = unicodedata.normalize("NFKD", key(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[\s\-_.·•:：,，/\\|()（）\[\]【】《》<>'\"“”‘’!！?？~～&＆;；、+]+", "", s)


def _names(artist: str | None) -> list[str]:
    """The people in an artist credit: "张也 & 阎维文", "A feat. B", "A, B / C" -> each name."""
    import re
    global _NAME_SPLIT
    if _NAME_SPLIT is None:
        _NAME_SPLIT = re.compile(r"\s*(?:&|＆|/|、|;|；|,|，|\bfeat\.?|\bft\.?|\bwith\b|\band\b|和|与|與)\s*", re.I)
    return [p for p in (_plain(x) for x in _NAME_SPLIT.split(artist or "")) if p]


def _volume_numbers(t: str | None) -> set[str]:
    """Numbers in a title that name a volume or disc (真发烧5 vs 真发烧4; CD1 vs CD2), not years or track counts."""
    import re
    global _NUMBERS
    if _NUMBERS is None:
        _NUMBERS = re.compile(r"(?:vol\.?|volume|第|cd|disc|disk|碟|之|集)\s*(\d{1,2})(?!\d)|(?<![\d])(\d{1,2})(?:\s*(?:集|辑|輯|部))|"
                              r"[^\d](\d)(?:ape|flac|mp3|wav)?$", re.I)
    out = set()
    for m in _NUMBERS.finditer(_plain(t) if t else ""):
        out.update(g.lstrip("0") or "0" for g in m.groups() if g)
    return out


def readable_tags(tags_json: str | None, issues_json: str | None) -> dict:
    """A file's tags as the app reads them: garbled GBK/Big5 fields replaced by their repaired proposal."""
    t = json.loads(tags_json or "{}")
    for i in json.loads(issues_json or "[]"):
        if i.get("field") and i.get("proposal"):
            t[i["field"]] = i["proposal"]
    return t


def ours(u: dict, file_tags: list[dict] | None) -> dict:
    """What the album says about itself: {"artists": [names from tags], "text": folder + label, "various": bool}."""
    artists = []
    for t in file_tags or []:
        for k in ("albumartist", "artist"):
            artists += _names(t.get(k))
    folder = (u.get("folder") or "").split("/")
    text = _plain(" ".join(folder[-3:] + [u.get("label") or ""]))
    various = any(a in VARIOUS for a in artists) or any(v in text for v in ("群星", "variousartists", "va."))
    return {"artists": sorted(set(a for a in artists if a not in VARIOUS)), "text": text, "various": various}


def artist_check(u: dict, top: dict) -> str:
    """agrees | differs | unknown -- does the candidate's artist belong to this album?"""
    me = u.get("_ours")
    if not me:
        return "unknown"
    theirs = _names(top.get("artist"))
    if not theirs:
        return "unknown"
    if all(t in VARIOUS for t in theirs):
        return "agrees" if me["various"] or len(me["artists"]) >= 3 else "unknown"
    names = [t for t in theirs if t not in VARIOUS and len(t) >= 2]
    if any(n in me["text"] or any(n in a or a in n for a in me["artists"]) for n in names):
        return "agrees"
    if me["artists"] or me["various"]:      # the album names its artists (or says "various"), and these are not them
        return "differs"
    return "unknown"                         # nothing to compare with: neither agreement nor a warning


def evidence(u: dict, top: dict) -> dict:
    """Facts the confidence rule uses, computed from the stored candidate."""
    n = u.get("n_tracks") or u["n_files"]
    extra = top.get("extra_tracks")
    if extra is None:
        extra = json.loads(top.get("extra_tracks_json") or "[]")
    media = top.get("_mapped_media") or set()
    disc_of_set = bool((top.get("mediums") or 1) > 1 and len(media) == 1)
    folder_leaf = (u.get("folder") or "").rsplit("/", 1)[-1]
    agrees = title_agrees(u.get("label"), top.get("album")) or title_agrees(folder_leaf, top.get("album"))
    mine_n = _volume_numbers(u.get("label")) | _volume_numbers(folder_leaf)
    theirs_n = _volume_numbers(top.get("album"))
    numbers_differ = bool(mine_n and theirs_n and not (mine_n & theirs_n))
    artist = artist_check(u, top) if (top.get("source") or "") != "Local" else "agrees"
    different = (top.get("source") or "") != "Local" and (numbers_differ or (not agrees and artist == "differs"))
    return {"n": n, "all_mapped": (top.get("n_mapped") or 0) >= n, "missing": len(extra),
            "disc_of_set": disc_of_set, "disc": next(iter(media)) if disc_of_set else None,
            "title_agrees": agrees and not numbers_differ, "artist": artist,
            "numbers_differ": numbers_differ, "different_album": different}


def confidence(u: dict, top: dict | None) -> str:
    """ready        fingerprints confirm (see module doc)
    likely       no fingerprint confirmation, but distance <= 0.15, every track mapped, none missing
                 (APE sample: all such matches were right) -- accept in bulk after a spot check
    needs_review any other plausible candidate, incl. one disc of a multi-disc set
    weak         distance > 0.5 and no fingerprints, but the album title clearly agrees
    no_match     nothing usable"""
    if u["status"] != "done":
        return u["status"]
    if not top:
        return "no_match"
    n = u.get("n_tracks") or u["n_files"]
    tried = min(n, CUE_FP_TRACKS) if u.get("kind") == "cue" else n
    share = (top["fingerprint_agrees"] or 0) / max(1, tried)
    if (top["distance"] < READY_DISTANCE and share >= READY_FP_SHARE) or \
            (top["distance"] < READY_DISTANCE_STRONG_FP and share >= STRONG_FP_SHARE):
        return "ready"                         # the audio itself confirms it, whatever the names say
    ev = evidence(u, top)
    if ev["different_album"]:
        return "no_match"                      # its title and artist (or volume) are another album's: Own info
    if ev["artist"] == "differs" and top["distance"] <= LIKELY_DISTANCE:
        return "needs_review"                  # close on paper, but by someone else: never accepted in bulk
    if top["distance"] <= LIKELY_DISTANCE and ev["all_mapped"] and ev["missing"] == 0:
        return "likely"
    if top["distance"] > NO_MATCH_DISTANCE and not top["fingerprint_agrees"]:
        if ev["disc_of_set"] and ev["all_mapped"] and ev["title_agrees"]:
            return "needs_review"      # one disc of a set: other discs' tracks are not "missing"
        return "weak" if ev["title_agrees"] else "no_match"
    return "needs_review"


def _mapped_media(con, unit_kind: str, candidate_id: int) -> set:
    table = "cand_cue_track" if unit_kind == "cue" else "cand_track"
    return {r[0] for r in con.execute(f"SELECT DISTINCT medium FROM {table} WHERE candidate_id=?", (candidate_id,))
            if r[0] is not None}


def units(con: sqlite3.Connection, root_id: int, folder: str = "") -> list[dict]:
    q = "SELECT * FROM unit WHERE root_id=?"
    args: tuple = (root_id,)
    if folder:
        q += " AND (folder=? OR (folder >= ? AND folder < ?))"
        args += (folder, folder + "/", folder + "/￿")
    from .units import gone_units
    gone = gone_units(con, root_id)
    rows = [dict(r) for r in con.execute(q + " ORDER BY folder, label", args)]
    # everything else in a few queries for all the albums at once, not a few per album: a folder of thousands of
    # albums must list in a second or two, not minutes (the window waits for it)
    ids = [u["id"] for u in rows]
    tops, local, dec = {}, {}, {}
    for chunk in (ids[i:i + 900] for i in range(0, len(ids), 900)):          # SQLite's limit on ? per query
        marks = ",".join("?" * len(chunk))
        for c in con.execute(
                f"SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY unit_id ORDER BY rank) AS _n FROM candidate "
                f"WHERE unit_id IN ({marks}) AND COALESCE(source,'')<>'Local') WHERE _n=1", chunk):
            c = dict(c)
            c.pop("_n")
            tops[c["unit_id"]] = c
        for c in con.execute(f"SELECT id, unit_id, album, artist, year FROM candidate WHERE unit_id IN ({marks}) "
                             f"AND source='Local' ORDER BY id", chunk):
            local.setdefault(c["unit_id"], {k: c[k] for k in ("id", "album", "artist", "year")})
        for d in con.execute(f"SELECT * FROM decision WHERE unit_id IN ({marks})", chunk):
            dec[d["unit_id"]] = dict(d)
    first: dict[int, list[int]] = {u["id"]: json.loads(u["file_ids"])[:4] for u in rows}
    fids = [f for v in first.values() for f in v]
    tags_of: dict[int, dict] = {}
    for chunk in (fids[i:i + 900] for i in range(0, len(fids), 900)):
        for fid, tj, ij in con.execute(f"SELECT id, tags_json, tag_issues_json FROM file WHERE id IN "
                                       f"({','.join('?' * len(chunk))})", chunk):
            tags_of[fid] = readable_tags(tj, ij)
    media: dict[int, set] = {}
    for table, kinds in (("cand_track", lambda k: k != "cue"), ("cand_cue_track", lambda k: k == "cue")):
        cids = [tops[u["id"]]["id"] for u in rows if u["id"] in tops and kinds(u["kind"])]
        for chunk in (cids[i:i + 900] for i in range(0, len(cids), 900)):
            for cid, m in con.execute(f"SELECT DISTINCT candidate_id, medium FROM {table} WHERE candidate_id IN "
                                      f"({','.join('?' * len(chunk))}) AND medium IS NOT NULL", chunk):
                media.setdefault(cid, set()).add(m)
    out = []
    for u in rows:
        u["presence"] = gone.get(u["id"], "here")          # here | retired | not_found
        u["n_files"] = len(json.loads(u["file_ids"]))
        u["top"] = tops.get(u["id"])
        u["_ours"] = ours(u, [tags_of[f] for f in first[u["id"]] if f in tags_of])
        if u["top"]:
            u["top"]["_mapped_media"] = media.get(u["top"]["id"], set())
        u["confidence"] = confidence(u, u["top"])
        if u["top"]:
            u["evidence"] = evidence(u, u["top"])
            u["top"].pop("_mapped_media")
        u["local"] = local.get(u["id"])
        u["decision"] = dec.get(u["id"])
        u.pop("_ours")
        out.append(u)
    # where each accepted name came from, so a name taken from the wrong release stands out in the list
    acc = {d["candidate_id"]: d for d in dec.values() if d.get("candidate_id")}
    cids = list(acc)
    for chunk in (cids[i:i + 900] for i in range(0, len(cids), 900)):
        for cid, src, album in con.execute(f"SELECT id, source, album FROM candidate WHERE id IN "
                                           f"({','.join('?' * len(chunk))})", chunk):
            acc[cid]["source"], acc[cid]["album"] = src, album
    return out


def _cue_slices(con: sqlite3.Connection, u: dict) -> dict:
    """{cue track number: [start, length]} from the cue sheet (read-only)."""
    import os
    from .. import cue as cuemod
    try:
        c = con.execute("SELECT f.fs_relpath, r.path AS root FROM file f JOIN root r ON r.id=f.root_id WHERE f.id=?",
                        (u["cue_file_id"],)).fetchone()
        audio = con.execute("SELECT relpath, duration FROM file WHERE id=?", (json.loads(u["file_ids"])[0],)).fetchone()
        sheet = cuemod.parse(os.path.join(c["root"], c["fs_relpath"]))
        f = cuemod.with_lengths(cuemod.file_for(sheet, audio["relpath"].rsplit("/", 1)[-1]), audio["duration"])
        return {t.number: [t.start, t.length] for t in f.tracks}
    except (OSError, TypeError, ValueError):
        return {}


def unit_detail(con: sqlite3.Connection, unit_id: int) -> dict | None:
    r = con.execute("SELECT * FROM unit WHERE id=?", (unit_id,)).fetchone()
    if not r:
        return None
    u = dict(r)
    ids = json.loads(u["file_ids"])
    u["n_files"] = len(ids)
    files = {f["id"]: dict(f) for f in con.execute(
        f"SELECT id, relpath, tags_json, tag_issues_json, duration FROM file WHERE id IN "
        f"({','.join('?' * len(ids))})", ids)}
    u["_ours"] = ours(u, [readable_tags(f["tags_json"], f["tag_issues_json"]) for f in files.values()])
    u["files"] = [{"id": i, "relpath": files[i]["relpath"], "duration": files[i]["duration"],
                   "tags": json.loads(files[i]["tags_json"] or "{}")} for i in ids if i in files]
    cands = []
    for c in con.execute("SELECT * FROM candidate WHERE unit_id=? ORDER BY rank", (unit_id,)):
        c = dict(c)
        c["penalties"] = json.loads(c.pop("penalties_json") or "[]")
        c["extra_tracks"] = json.loads(c.pop("extra_tracks_json") or "[]")
        c["extra_items"] = json.loads(c.pop("extra_items_json") or "[]")
        c["origin"] = json.loads(c.pop("origin_json") or "null")
        if u["kind"] == "cue":
            c["tracks"] = [{**dict(t), "file_id": None, "label": f"{t['cue_track']:02d}. {t['cue_title'] or ''}"}
                           for t in con.execute("SELECT * FROM cand_cue_track WHERE candidate_id=? "
                                                "ORDER BY cue_track", (c["id"],))]
        else:
            c["tracks"] = [dict(t) for t in con.execute(
                "SELECT * FROM cand_track WHERE candidate_id=? ORDER BY medium, medium_index", (c["id"],))]
        cands.append(c)
    u["candidates"] = cands
    if u["kind"] == "cue":
        u["cue_slices"] = _cue_slices(con, u)        # where each cue track sits in the image (preview)
        u["audio_file_id"] = ids[0]
    if u["kind"] == "cue" and cands:
        u["n_cue_tracks"] = max(len(c["tracks"]) for c in cands)
    for c in cands:
        c["_mapped_media"] = {t["medium"] for t in c["tracks"] if t.get("medium") is not None}
        c["evidence"] = evidence(u, c)
    online = [c for c in cands if c["source"] != "Local"]
    u["confidence"] = confidence(u, online[0] if online else None)
    for c in cands:
        c.pop("_mapped_media")
    u.pop("_ours")
    d = con.execute("SELECT * FROM decision WHERE unit_id=?", (unit_id,)).fetchone()
    u["decision"] = dict(d) if d else None
    return u
