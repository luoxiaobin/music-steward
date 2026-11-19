"""Match ONE unit in a fresh process: python -m music_steward.recognize.worker --db X --unit N

A new process per unit is the strongest isolation from beets' process-global state
(M0 finding F1). Writes results straight into the app DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

from .. import db


def _match_cue(con, u, audio_path: str, key, search):
    from .. import cue as cuemod
    from . import beets_adapter as A
    c = con.execute("SELECT f.fs_relpath, r.path AS root FROM file f JOIN root r ON r.id=f.root_id WHERE f.id=?",
                    (u["cue_file_id"],)).fetchone()
    sheet = cuemod.parse(os.path.join(c["root"], c["fs_relpath"]))
    audio = con.execute("SELECT duration, audio_offset FROM file WHERE id=?", (json.loads(u["file_ids"])[0],)).fetchone()
    f = max(sheet.files, key=lambda x: len(x.tracks))
    cuemod.with_lengths(f, audio["duration"])
    tracks = [{"number": t.number, "title": t.title, "performer": t.performer, "start": t.start, "length": t.length}
              for t in f.tracks]
    return A.match_cue(audio_path, {"title": sheet.title, "performer": sheet.performer, "date": sheet.date},
                       tracks, key, search, offset=audio["audio_offset"] or 0)


MAX_TEXT = 500


def _short(s):
    """Junk catalogue entries exist (a Discogs release whose track title is 274,000 characters)."""
    return s[:MAX_TEXT] + "…" if isinstance(s, str) and len(s) > MAX_TEXT else s


def run_unit(db_path: str, unit_id: int) -> int:
    con = db.connect(db_path)
    u = con.execute("SELECT * FROM unit WHERE id=?", (unit_id,)).fetchone()
    files = {r["id"]: r for r in con.execute(
        f"SELECT f.id, f.fs_relpath, r.path AS root FROM file f JOIN root r ON r.id=f.root_id "
        f"WHERE f.id IN ({','.join('?' * len(json.loads(u['file_ids'])))})", json.loads(u["file_ids"]))}
    path_of = {fid: os.path.join(r["root"], r["fs_relpath"]) for fid, r in files.items()}
    id_of = {p: fid for fid, p in path_of.items()}
    con.execute("UPDATE unit SET status='running', started_at=?, error=NULL WHERE id=?", (time.time(), unit_id))
    con.commit()
    try:
        from . import beets_adapter as A
        key = os.environ.get("ACOUSTID_API_KEY")
        paths = [path_of[i] for i in sorted(path_of)]
        search = json.loads(u["search_json"]) if u["search_json"] else None
        if u["kind"] == "cue":
            res = _match_cue(con, u, paths[0], key, search)
        else:
            res = (A.match_track(paths[0], key, search) if u["kind"] == "track"
                   else A.match_album(paths, key, search))
        from .results import candidate_for, release_key
        dec = con.execute("SELECT * FROM decision WHERE unit_id=?", (unit_id,)).fetchone()
        kept_key = None
        if dec is not None and dec["action"] == "accept":
            kept_key = dec["release_key"] or (release_key(con, dec["candidate_id"]) if dec["candidate_id"] else None)
            con.execute("UPDATE decision SET candidate_id=NULL WHERE unit_id=?", (unit_id,))
        con.execute("DELETE FROM candidate WHERE unit_id=?", (unit_id,))
        for c in res["candidates"]:
            for k in ("album", "artist", "label"):
                c[k] = _short(c.get(k))
            for t in c["tracks"]:
                t["title"], t["artist"] = _short(t.get("title")), _short(t.get("artist"))
            cid = con.execute(
                "INSERT INTO candidate(unit_id, rank, source, album_id, album, artist, year, country, label, "
                "media, mediums, distance, penalties_json, extra_items_json, extra_tracks_json, "
                "fingerprint_agrees, n_mapped) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (unit_id, c["rank"], c["source"], c["album_id"], c["album"], c["artist"], c["year"],
                 c["country"], c["label"], c["media"], c["mediums"], c["distance"],
                 json.dumps(c["penalties"]), json.dumps([id_of.get(p) for p in c["extra_items"]]),
                 json.dumps(c["extra_tracks"], ensure_ascii=False), c["fingerprint_agrees"],
                 c["n_mapped"])).lastrowid
            for t in c["tracks"]:
                if "cue_track" in t:
                    con.execute("INSERT INTO cand_cue_track VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                (cid, t["cue_track"], t["cue_title"], t["recording_id"], t["release_track_id"],
                                 t["index"], t["medium"], t["medium_index"], t["title"], t["artist"], t["length"],
                                 t["track_distance"]))
                    continue
                con.execute("INSERT INTO cand_track VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, id_of[t["path"]], t["recording_id"], t["release_track_id"], t["index"],
                             t["medium"], t["medium_index"], t["title"], t["artist"], t["length"],
                             t["track_distance"]))
        notice = None
        if dec is not None and dec["action"] == "accept":
            if kept_key == "local":         # the user organizes this album from its own info
                from . import local
                new_cid = local.add(con, unit_id)
            else:
                new_cid = candidate_for(con, unit_id, kept_key) if kept_key else None
            if new_cid:
                con.execute("UPDATE decision SET candidate_id=?, release_key=? WHERE unit_id=?",
                            (new_cid, kept_key, unit_id))
            else:
                con.execute("DELETE FROM decision WHERE unit_id=?", (unit_id,))
                notice = "Your earlier choice is no longer among the candidates; please review again."
        con.execute("UPDATE unit SET notice=? WHERE id=?", (notice, unit_id))
        con.execute("UPDATE unit SET status='done', recommendation=?, acoustid_used=?, files_linked=?, "
                    "repaired=?, n_tracks=?, finished_at=? WHERE id=?",
                    (res["recommendation"], int(res["acoustid_used"]), res["files_fingerprint_linked"],
                     res.get("repaired", 0), res.get("n_tracks"), time.time(), unit_id))
        con.commit()
        return 0
    except Exception as e:
        con.rollback()
        con.execute("UPDATE unit SET status='error', error=?, finished_at=? WHERE id=?",
                    (f"{type(e).__name__}: {e}"[:500] + "\n" + traceback.format_exc()[-1500:],
                     time.time(), unit_id))
        con.commit()
        return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--unit", type=int, required=True)
    a = ap.parse_args()
    sys.exit(run_unit(a.db, a.unit))
