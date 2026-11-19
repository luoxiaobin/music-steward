"""Turn a folder of the inventory into recognition units (albums or single tracks).

Rules (from the NAS collection):
  * disc sub-folders ("CD 1", "Disc 2", "CD2") merge into their parent: one album
  * a folder is one album when one album tag covers >= 80% of its files, or it has
    no album tags at all (an untagged album folder)
  * otherwise it is a folder of singles (e.g. 张也-单曲, 341 files): album tags with
    >= 3 files become albums, every other file is matched on its own as a track
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict

DISC_DIR = re.compile(r"^(cd|disc|disk|碟)\s*[-_ ]?\s*\d{1,2}$", re.I)
ALBUM_SHARE = 0.8      # one album tag on this share of a folder's files -> the folder is that album
MIN_ALBUM_FILES = 3    # in a singles folder, smaller album groups are matched track by track


def _album_tag(tags_json, issues_json) -> str | None:
    t = json.loads(tags_json or "{}")
    fixes = {i["field"]: i.get("proposal") for i in json.loads(issues_json or "[]") if i.get("proposal")}
    a = fixes.get("album") or t.get("album")
    return a.strip() if a and a.strip() else None


def plan_units(con: sqlite3.Connection, root_id: int, subpath: str = "") -> list[dict]:
    q = ("SELECT id, relpath, tags_json, tag_issues_json FROM file WHERE root_id=? AND removed_at IS NULL "
         "AND kind='audio' AND probe_status='ok'")
    args: tuple = (root_id,)
    if subpath:
        q += " AND (relpath >= ? AND relpath < ?)"
        args += (subpath + "/", subpath + "/￿")
    by_folder: dict[str, list] = defaultdict(list)
    merged: set[str] = set()
    for r in con.execute(q + " ORDER BY relpath", args):
        folder = r["relpath"].rsplit("/", 1)[0] if "/" in r["relpath"] else ""
        parent, _, leaf = folder.rpartition("/")
        if DISC_DIR.match(leaf):
            folder = parent  # CD 1 / CD 2 belong to the album folder
            merged.add(folder)
        by_folder[folder].append(r)
    units = []

    def name_of(folder):
        return folder.rsplit("/", 1)[-1] or "(root)"
    for folder, rows in by_folder.items():
        albums = defaultdict(list)
        for r in rows:
            albums[_album_tag(r["tags_json"], r["tag_issues_json"])].append(r)
        named = {a: rs for a, rs in albums.items() if a}
        dominant = max(named.items(), key=lambda kv: len(kv[1]), default=(None, []))
        if folder in merged or not named or len(dominant[1]) >= ALBUM_SHARE * len(rows):
            label = dominant[0] if dominant[0] and len(dominant[1]) >= ALBUM_SHARE * len(rows) else name_of(folder)
            units.append({"folder": folder, "kind": "album", "label": label, "key": f"album:{folder}",
                          "file_ids": [r["id"] for r in rows]})
            continue
        for album, rs in albums.items():
            if album and len(rs) >= MIN_ALBUM_FILES:
                units.append({"folder": folder, "kind": "album", "label": album,
                              "key": f"album:{folder}:{album}", "file_ids": [r["id"] for r in rs]})
            else:
                for r in rs:
                    units.append({"folder": folder, "kind": "track", "label": r["relpath"].rsplit("/", 1)[-1],
                                  "key": f"track:{r['relpath']}", "file_ids": [r["id"]]})
    return units


def enqueue(con: sqlite3.Connection, root_id: int, units: list[dict], requeue: bool = False) -> dict:
    """Insert units; existing ones (same key) keep their results unless requeue."""
    c = {"queued": 0, "already_done": 0, "requeued": 0}
    now = time.time()
    for u in units:
        row = con.execute("SELECT id, status, file_ids FROM unit WHERE root_id=? AND key=?",
                          (root_id, u["key"])).fetchone()
        ids = json.dumps(sorted(u["file_ids"]))
        if row is None:
            con.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status, queued_at) "
                        "VALUES(?,?,?,?,?,?, 'queued', ?)",
                        (root_id, u["folder"], u["label"], u["kind"], u["key"], ids, now))
            c["queued"] += 1
        elif requeue or row["file_ids"] != ids or row["status"] == "error":
            con.execute("UPDATE unit SET status='queued', file_ids=?, label=?, queued_at=?, error=NULL "
                        "WHERE id=?", (ids, u["label"], now, row["id"]))
            con.execute("DELETE FROM decision WHERE unit_id=?", (row["id"],))
            c["requeued"] += 1
        else:
            c["already_done" if row["status"] == "done" else "queued"] += 1
    con.commit()
    return c
