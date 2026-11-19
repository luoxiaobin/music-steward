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


def confidence(u: dict, top: dict | None) -> str:
    if u["status"] != "done":
        return u["status"]
    if not top:
        return "no_match"
    share = (top["fingerprint_agrees"] or 0) / max(1, u.get("n_tracks") or u["n_files"])
    if (top["distance"] < READY_DISTANCE and share >= READY_FP_SHARE) or \
            (top["distance"] < READY_DISTANCE_STRONG_FP and share >= STRONG_FP_SHARE):
        return "ready"
    if top["distance"] > NO_MATCH_DISTANCE and not top["fingerprint_agrees"]:
        return "no_match"
    return "needs_review"


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
        u["confidence"] = confidence(u, u["top"])
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
    u["confidence"] = confidence(u, cands[0] if cands else None)
    d = con.execute("SELECT * FROM decision WHERE unit_id=?", (unit_id,)).fetchone()
    u["decision"] = dict(d) if d else None
    return u
