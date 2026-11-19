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
    c = con.execute("SELECT album_id FROM candidate WHERE id=?", (candidate_id,)).fetchone()
    if c is None:
        return None
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
    t = (t or "").casefold()
    t = re.sub(r"[\s\-_.·•:：,，/\\|()（）\[\]【】《》<>'\"“”‘’!！?？~～]+", "", t)
    return re.sub(r"(cd|disc|disk)\d+$", "", t)


def title_agrees(a: str | None, b: str | None) -> bool:
    from difflib import SequenceMatcher
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return False
    return x in y or y in x or SequenceMatcher(None, x, y).ratio() >= TITLE_AGREES


def evidence(u: dict, top: dict) -> dict:
    """Facts the confidence rule uses, computed from the stored candidate."""
    n = u.get("n_tracks") or u["n_files"]
    extra = top.get("extra_tracks")
    if extra is None:
        extra = json.loads(top.get("extra_tracks_json") or "[]")
    media = top.get("_mapped_media") or set()
    disc_of_set = bool((top.get("mediums") or 1) > 1 and len(media) == 1)
    return {"n": n, "all_mapped": (top.get("n_mapped") or 0) >= n, "missing": len(extra),
            "disc_of_set": disc_of_set, "disc": next(iter(media)) if disc_of_set else None,
            "title_agrees": title_agrees(u.get("label"), top.get("album"))}


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
        return "ready"
    ev = evidence(u, top)
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
    out = []
    for r in con.execute(q + " ORDER BY folder, label", args):
        u = dict(r)
        u["n_files"] = len(json.loads(u["file_ids"]))
        c = con.execute("SELECT * FROM candidate WHERE unit_id=? ORDER BY rank LIMIT 1", (u["id"],)).fetchone()
        u["top"] = dict(c) if c else None
        if u["top"]:
            u["top"]["_mapped_media"] = _mapped_media(con, u["kind"], u["top"]["id"])
        u["confidence"] = confidence(u, u["top"])
        if u["top"]:
            u["evidence"] = evidence(u, u["top"])
            u["top"].pop("_mapped_media")
        d = con.execute("SELECT * FROM decision WHERE unit_id=?", (u["id"],)).fetchone()
        u["decision"] = dict(d) if d else None
        out.append(u)
    return out


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
    u["files"] = [{"id": i, "relpath": files[i]["relpath"], "duration": files[i]["duration"],
                   "tags": json.loads(files[i]["tags_json"] or "{}")} for i in ids if i in files]
    cands = []
    for c in con.execute("SELECT * FROM candidate WHERE unit_id=? ORDER BY rank", (unit_id,)):
        c = dict(c)
        c["penalties"] = json.loads(c.pop("penalties_json") or "[]")
        c["extra_tracks"] = json.loads(c.pop("extra_tracks_json") or "[]")
        c["extra_items"] = json.loads(c.pop("extra_items_json") or "[]")
        if u["kind"] == "cue":
            c["tracks"] = [{**dict(t), "file_id": None, "label": f"{t['cue_track']:02d}. {t['cue_title'] or ''}"}
                           for t in con.execute("SELECT * FROM cand_cue_track WHERE candidate_id=? "
                                                "ORDER BY cue_track", (c["id"],))]
        else:
            c["tracks"] = [dict(t) for t in con.execute(
                "SELECT * FROM cand_track WHERE candidate_id=? ORDER BY medium, medium_index", (c["id"],))]
        cands.append(c)
    u["candidates"] = cands
    if u["kind"] == "cue" and cands:
        u["n_cue_tracks"] = max(len(c["tracks"]) for c in cands)
    for c in cands:
        c["_mapped_media"] = {t["medium"] for t in c["tracks"] if t.get("medium") is not None}
        c["evidence"] = evidence(u, c)
    u["confidence"] = confidence(u, cands[0] if cands else None)
    for c in cands:
        c.pop("_mapped_media")
    d = con.execute("SELECT * FROM decision WHERE unit_id=?", (unit_id,)).fetchone()
    u["decision"] = dict(d) if d else None
    return u
