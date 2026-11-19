"""Read recognition results back, with the app's own confidence rule.

Confidence (M0 evidence, to be tuned): beets' 'strong' (< 0.04) marked nothing on the
real collection, while correct releases sat at 0.09-0.30 with most tracks
fingerprint-linked to them.
  ready         most files' fingerprints agree with the top candidate AND distance < 0.15
  needs_review  any other candidate
  no_match      no candidates, or top distance > 0.5 with no fingerprint agreement
"""
from __future__ import annotations

import json
import sqlite3

READY_DISTANCE = 0.15
READY_FP_SHARE = 0.6
NO_MATCH_DISTANCE = 0.5


def confidence(u: dict, top: dict | None) -> str:
    if u["status"] != "done":
        return u["status"]
    if not top:
        return "no_match"
    share = (top["fingerprint_agrees"] or 0) / max(1, u["n_files"])
    if top["distance"] < READY_DISTANCE and share >= READY_FP_SHARE:
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
        c["tracks"] = [dict(t) for t in con.execute(
            "SELECT * FROM cand_track WHERE candidate_id=? ORDER BY medium, medium_index", (c["id"],))]
        cands.append(c)
    u["candidates"] = cands
    u["confidence"] = confidence(u, cands[0] if cands else None)
    d = con.execute("SELECT * FROM decision WHERE unit_id=?", (unit_id,)).fetchone()
    u["decision"] = dict(d) if d else None
    return u
