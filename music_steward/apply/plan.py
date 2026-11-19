"""Build a frozen plan from accepted review decisions.

Nothing on disk changes here. Each item records the source's size + mtime at plan time
(apply refuses a source that changed since), the destination path from the naming
rules, and the tag values to write to the COPY. Conflicts are flagged, never resolved
by overwriting: existing destination, two items on the same name (case/NFC-insensitive),
or a source that changed since it was scanned.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from .naming import album_dir, collision_key, safe_component, track_name

COVER = re.compile(r"^(cover|folder|front|albumart)([ _-]?(large|front))?\.(jpe?g|png)$", re.I)


def _tags_for(c, t, kind) -> dict:
    total_discs = c["mediums"] or 1
    is_mb = (c.get("source") or "MusicBrainz") == "MusicBrainz"   # never put Deezer/Discogs ids in MB tags
    return {
        "title": t["title"], "artist": t["artist"] or c["artist"],
        "album": c["album"] if kind == "album" else None,
        "albumartist": c["artist"] if kind == "album" else None,
        "track": t["medium_index"] or t["track_index"] if kind == "album" else None,
        "disc": f"{t['medium']}/{total_discs}" if kind == "album" and total_discs > 1 else None,
        "date": c["year"], "mb_trackid": t["recording_id"] if is_mb else None,
        "mb_albumid": c["album_id"] if is_mb else None,
    }


def _cue_items(con, u, c, adir, root_path, items, new_stem_of):
    """A cue-sheet image album: copy the image (album-level tags) and a rewritten cue sheet
    (new FILE name, album + track titles from the release, original encoding). No splitting."""
    from .. import cue as cuemod
    from .naming import ext_for
    f = con.execute("SELECT * FROM file WHERE id=?", (json.loads(u["file_ids"])[0],)).fetchone()
    cf = con.execute("SELECT * FROM file WHERE id=?", (u["cue_file_id"],)).fetchone()
    stem = safe_component(c["album"], "Album")
    image_name = stem + ext_for(f["codec"], f["ext"])
    is_mb = (c.get("source") or "MusicBrainz") == "MusicBrainz"
    items.append({"unit_id": u["id"], "file_id": f["id"], "kind": "audio",
                  "src": os.path.join(root_path, f["fs_relpath"]), "dest_rel": f"{adir}/{image_name}",
                  "codec": f["codec"], "size": f["size"], "mtime": f["mtime_ns"],
                  "tags": {"album": c["album"], "albumartist": c["artist"], "date": c["year"],
                           "mb_albumid": c["album_id"] if is_mb else None}})
    sheet = cuemod.parse(os.path.join(root_path, cf["fs_relpath"]))
    tracks = {str(t["cue_track"]): {"title": t["title"], "performer": t["artist"] or c["artist"]}
              for t in con.execute("SELECT * FROM cand_cue_track WHERE candidate_id=?", (c["id"],))}
    items.append({"unit_id": u["id"], "file_id": cf["id"], "kind": "cue",
                  "src": os.path.join(root_path, cf["fs_relpath"]), "dest_rel": f"{adir}/{stem}.cue",
                  "codec": None, "size": cf["size"], "mtime": cf["mtime_ns"],
                  "tags": {"cue_rewrite": {"files": {fl.name: image_name for fl in sheet.files},
                                           "album": {"title": c["album"], "performer": c["artist"]},
                                           "tracks": tracks}}})
    new_stem_of[f["relpath"].rsplit(".", 1)[0]] = f"{adir}/{stem}"


class PlanError(Exception):
    pass


def check_destination(con: sqlite3.Connection, dest: Path):
    """The destination must never be inside a source root: sources are read-only."""
    if not dest.is_absolute():
        raise PlanError("destination must be an absolute path")
    d = os.path.realpath(dest)
    for (r,) in con.execute("SELECT path FROM root"):
        rr = os.path.realpath(r)
        if d == rr or d.startswith(rr.rstrip("/") + "/") or rr.startswith(d.rstrip("/") + "/"):
            raise PlanError(f"destination overlaps the source root {r}; choose a folder outside your collection")


def build(con: sqlite3.Connection, dest_root: str, root_id: int, scope: str = "") -> int:
    dest = Path(dest_root).expanduser().resolve()
    check_destination(con, dest)
    q = ("SELECT u.*, d.candidate_id, d.overrides_json FROM unit u JOIN decision d ON d.unit_id=u.id "
         "WHERE d.action='accept' AND u.root_id=?")
    args: tuple = (root_id,)
    if scope:
        q += " AND (u.folder=? OR (u.folder >= ? AND u.folder < ?))"
        args += (scope, scope + "/", scope + "/￿")
    root_path = con.execute("SELECT path FROM root WHERE id=?", (root_id,)).fetchone()["path"]
    items = []
    for u in con.execute(q, args).fetchall():
        c = dict(con.execute("SELECT * FROM candidate WHERE id=?", (u["candidate_id"],)).fetchone())
        tracks = {t["file_id"]: dict(t) for t in con.execute("SELECT * FROM cand_track WHERE candidate_id=?", (c["id"],))}
        ov = json.loads(u["overrides_json"] or "{}")
        for k_from, k_to in (("album", "album"), ("albumartist", "artist"), ("year", "year")):
            if ov.get(k_from) not in (None, ""):
                c[k_to] = int(ov[k_from]) if k_from == "year" else ov[k_from]
        for fid, title in (ov.get("titles") or {}).items():
            if int(fid) in tracks and title:
                tracks[int(fid)]["title"] = title
        adir = (album_dir(c["artist"], c["album"], c["year"]) if u["kind"] in ("album", "cue")
                else f"{safe_component(c['artist'], 'Unknown Artist')}/Singles")
        new_stem_of = {}
        if u["kind"] == "cue":
            _cue_items(con, u, c, adir, root_path, items, new_stem_of)
        for fid in ([] if u["kind"] == "cue" else json.loads(u["file_ids"])):
            f = con.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
            t = tracks.get(fid)
            if t is None:
                continue  # file not on the chosen release: stays out of the plan (reported)
            name = track_name(t["title"], t["medium_index"] or t["track_index"], t["medium"],
                              c["mediums"], f["codec"], f["ext"])
            new_stem_of[f["relpath"].rsplit(".", 1)[0]] = f"{adir}/{name.rsplit('.', 1)[0]}"
            items.append({"unit_id": u["id"], "file_id": fid, "kind": "audio",
                          "src": os.path.join(root_path, f["fs_relpath"]), "dest_rel": f"{adir}/{name}",
                          "codec": f["codec"], "tags": _tags_for(c, t, u["kind"]),
                          "size": f["size"], "mtime": f["mtime_ns"]})
        # side files that live next to the unit's audio
        folders = {f.rsplit("/", 1)[0] if "/" in f else "" for f in new_stem_of}
        for folder in sorted(folders):  # deterministic: CD 1 before CD 2
            prefix = f"{folder}/" if folder else ""
            for s in con.execute("SELECT * FROM file WHERE root_id=? AND removed_at IS NULL AND kind IN ('lrc','image') "
                                 "AND relpath >= ? AND relpath < ?", (root_id, prefix, prefix + "￿")):
                if "/" in s["relpath"][len(prefix):]:
                    continue
                stem = s["relpath"].rsplit(".", 1)[0]
                if s["kind"] == "lrc" and stem in new_stem_of:
                    dest_rel = new_stem_of[stem] + ".lrc"
                elif s["kind"] == "image" and u["kind"] in ("album", "cue") and COVER.match(s["relpath"].rsplit("/", 1)[-1]):
                    dest_rel = f"{adir}/{safe_component(s['relpath'].rsplit('/', 1)[-1])}"
                else:
                    continue
                items.append({"unit_id": u["id"], "file_id": s["id"], "kind": s["kind"],
                              "src": os.path.join(root_path, s["fs_relpath"]), "dest_rel": dest_rel,
                              "codec": None, "tags": {}, "size": s["size"], "mtime": s["mtime_ns"]})
    items, dropped = _dedupe_images(items)
    # conflicts
    seen = defaultdict(list)
    for it in items:
        seen[collision_key(it["dest_rel"])].append(it)
    for it in items:
        it["status"], it["note"] = "ok", None
        st = os.stat(it["src"]) if os.path.exists(it["src"]) else None
        if st is None or st.st_size != it["size"] or st.st_mtime_ns != it["mtime"]:
            it["status"], it["note"] = "source_changed", "source changed or missing since the scan; re-scan"
        elif (dest / it["dest_rel"]).exists():
            it["status"], it["note"] = "conflict_existing", "a file already exists at the destination"
        elif len(seen[collision_key(it["dest_rel"])]) > 1:
            it["status"], it["note"] = "conflict_duplicate", "another item in this plan has the same destination name"
    summary = {"items": len(items), "ok": sum(i["status"] == "ok" for i in items),
               "duplicate_images_skipped": dropped,
               "blocked": sum(i["status"] != "ok" for i in items),
               "bytes": sum(i["size"] for i in items if i["status"] == "ok"),
               "albums": len({i["unit_id"] for i in items})}
    pid = con.execute("INSERT INTO plan(created_at, dest_root, root_id, scope, summary_json) VALUES(?,?,?,?,?)",
                      (time.time(), str(dest), root_id, scope, json.dumps(summary))).lastrowid
    con.executemany("INSERT INTO plan_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(pid, n, i["unit_id"], i["file_id"], i["kind"], i["src"], i["dest_rel"], i["codec"],
                      json.dumps(i["tags"], ensure_ascii=False), i["size"], i["mtime"], i["status"], i["note"])
                     for n, i in enumerate(items)])
    con.commit()
    return pid


def _dedupe_images(items: list[dict]) -> tuple[list[dict], int]:
    """Cover images from several disc folders (CD 1/Folder.jpg, CD 2/Folder.jpg) land on one
    name. Keep one copy when they are byte-identical; when they differ, keep both and name
    the later ones after their own folder ("Folder (CD 2).jpg"). Never a blocking conflict."""
    from ..hashing import file_sha256
    groups = defaultdict(list)
    for it in items:
        if it["kind"] == "image":
            groups[(it["unit_id"], collision_key(it["dest_rel"]))].append(it)
    drop = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        kept_hashes = {file_sha256(group[0]["src"]): group[0]}
        for it in group[1:]:
            h = file_sha256(it["src"])
            if h in kept_hashes:
                drop.add(id(it))
                continue
            kept_hashes[h] = it
            folder = os.path.basename(os.path.dirname(it["src"]))
            stem, ext = os.path.splitext(it["dest_rel"])
            it["dest_rel"] = f"{stem} ({safe_component(folder)}){ext}"
    return [i for i in items if id(i) not in drop], len(drop)


def load(con: sqlite3.Connection, plan_id: int) -> dict:
    p = dict(con.execute("SELECT * FROM plan WHERE id=?", (plan_id,)).fetchone())
    p["summary"] = json.loads(p.pop("summary_json"))
    p["items"] = [{**dict(r), "tags": json.loads(r["tags_json"] or "{}")} for r in con.execute(
        "SELECT * FROM plan_item WHERE plan_id=? ORDER BY seq", (plan_id,))]
    for it in p["items"]:
        it.pop("tags_json", None)
    p["runs"] = [dict(r) for r in con.execute("SELECT * FROM run WHERE plan_id=? ORDER BY id", (plan_id,))]
    return p
