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


def _cue_units(con: sqlite3.Connection, root_id: int, subpath: str) -> tuple[list[dict], set[int]]:
    """Image albums: a cue sheet whose FILE names an audio file in the same folder (the
    extension may differ: cues often say .wav for an .ape) and lists more than one track."""
    import os
    from .. import cue as cuemod
    from ..paths import nfc
    root = con.execute("SELECT path FROM root WHERE id=?", (root_id,)).fetchone()["path"]
    q = "SELECT id, relpath, fs_relpath FROM file WHERE root_id=? AND removed_at IS NULL AND kind='cue'"
    args: tuple = (root_id,)
    if subpath:
        q += " AND relpath >= ? AND relpath < ?"
        args += (subpath + "/", subpath + "/\uffff")
    units, used = [], set()
    for c in con.execute(q, args).fetchall():
        folder = c["relpath"].rsplit("/", 1)[0] if "/" in c["relpath"] else ""
        prefix = f"{folder}/" if folder else ""
        audio = {}
        for a in con.execute("SELECT id, relpath, duration FROM file WHERE root_id=? AND removed_at IS NULL AND "
                             "kind='audio' AND probe_status='ok' AND relpath >= ? AND relpath < ?",
                             (root_id, prefix, prefix + "\uffff")):
            if "/" not in a["relpath"][len(prefix):]:
                audio[a["relpath"][len(prefix):]] = a
        try:
            sheet = cuemod.parse(os.path.join(root, c["fs_relpath"]))
        except (OSError, ValueError):
            continue
        # an IMAGE cue has few FILE entries and many tracks (1 file / 12 tracks; 2 files / 24 for a
        # 2-CD image). A per-track cue (one FILE per track file) is not an image album.
        if len(sheet.files) > max(1, len(sheet.tracks) // 4):
            continue
        for f in sheet.files:
            if len(f.tracks) < 2:
                continue
            want = nfc(f.name).casefold()
            want_stem = os.path.splitext(want)[0]
            hit = next((a for n, a in audio.items() if n.casefold() == want), None) or \
                next((a for n, a in audio.items() if os.path.splitext(n.casefold())[0] == want_stem), None) or \
                (next(iter(audio.values())) if len(audio) == 1 else None)
            if hit is None or hit["id"] in used:
                continue
            used.add(hit["id"])
            units.append({"folder": folder, "kind": "cue", "label": sheet.title or folder.rsplit("/", 1)[-1],
                          "key": f"cue:{c['relpath']}:{hit['relpath']}", "file_ids": [hit["id"]],
                          "cue_file_id": c["id"]})
    return units, used


def plan_units(con: sqlite3.Connection, root_id: int, subpath: str = "") -> list[dict]:
    q = ("SELECT id, relpath, tags_json, tag_issues_json FROM file WHERE root_id=? AND removed_at IS NULL "
         "AND kind='audio' AND probe_status='ok'")
    args: tuple = (root_id,)
    if subpath:
        q += " AND (relpath >= ? AND relpath < ?)"
        args += (subpath + "/", subpath + "/￿")
    cue_units, in_cue = _cue_units(con, root_id, subpath)
    by_folder: dict[str, list] = defaultdict(list)
    merged: set[str] = set()
    for r in con.execute(q + " ORDER BY relpath", args):
        if r["id"] in in_cue:
            continue  # the long file of an image album is matched through its cue sheet
        folder = r["relpath"].rsplit("/", 1)[0] if "/" in r["relpath"] else ""
        parent, _, leaf = folder.rpartition("/")
        if DISC_DIR.match(leaf):
            folder = parent  # CD 1 / CD 2 belong to the album folder
            merged.add(folder)
        by_folder[folder].append(r)
    units = list(cue_units)

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


CHANGED = "its audio or cue sheet changed since you decided; review this album again"
CUE_REPLACED = "its cue sheet was replaced since you decided; review this album again"


def flag_changed(con: sqlite3.Connection, file_ids: list[int]) -> int:
    """Files whose content changed (the scan saw a different content_id): their albums go back to
    the queue, and a decision made on the old audio is flagged -- kept, so it can be confirmed, but
    left out of plans until the album is decided again. Returns the number of albums flagged."""
    if not file_ids:
        return 0
    marks = ",".join("?" * len(file_ids))
    hit = [r[0] for r in con.execute(
        f"SELECT DISTINCT u.id FROM unit u, json_each(u.file_ids) j WHERE j.value IN ({marks}) "
        f"UNION SELECT id FROM unit WHERE cue_file_id IN ({marks})", (*file_ids, *file_ids))]
    for uid in hit:
        _flag(con, uid, CHANGED)
    return len(hit)


def _flag(con: sqlite3.Connection, uid: int, reason: str):
    con.execute("UPDATE unit SET status='queued', queued_at=? WHERE id=? AND status<>'running'", (time.time(), uid))
    con.execute("UPDATE decision SET stale_reason=? WHERE unit_id=?", (reason, uid))


def enqueue(con: sqlite3.Connection, root_id: int, units: list[dict], requeue: bool = False,
            scope: str | None = None) -> dict:
    """Insert units; existing ones (same key) keep their results unless requeue.
    With scope (the folder that was planned, "" = whole root), units in that folder that the
    new plan no longer contains are removed -- e.g. a folder now recognised as a cue album --
    so a file can never sit in two units (and be copied twice)."""
    c = {"queued": 0, "already_done": 0, "requeued": 0, "removed_stale": 0, "moved": 0}
    now = time.time()
    # An album whose folder was renamed or moved has a new key but the same files (file rows follow
    # their content, ADR 0002): it is the same album -- move the unit, keeping its matches and decision.
    # Albums are matched by their FILES first and keys sorted out after, so a new album placed in the
    # old folder (or two folders swapped) cannot take over a moved album's unit and decision (Codex
    # review on #6). All of this happens before stale units are removed below.
    moves = []
    for u in units:
        ids = json.dumps(sorted(u["file_ids"]))
        rows = con.execute("SELECT id, key, cue_file_id FROM unit WHERE root_id=? AND file_ids=? AND kind=?",
                           (root_id, ids, u["kind"])).fetchall()
        if rows and not any(r["key"] == u["key"] for r in rows):
            moves.append((rows[0], u))
    moving = {m["id"] for m, _ in moves}
    for m, _ in moves:                       # free the old keys first (a swap of two folders included)
        con.execute("UPDATE unit SET key=? WHERE id=?", (f"moving:{m['id']}", m["id"]))
    for m, u in moves:
        holder = con.execute("SELECT id FROM unit WHERE root_id=? AND key=?", (root_id, u["key"])).fetchone()
        if holder and holder["id"] not in moving:
            # the unit that held this key has other files: it is not this album (removed below as stale
            # if its files are gone, like any album whose folder no longer holds it)
            con.execute("UPDATE unit SET key=? WHERE id=?", (f"replaced:{holder['id']}", holder["id"]))
        con.execute("UPDATE unit SET key=?, folder=?, label=?, cue_file_id=? WHERE id=?",
                    (u["key"], u["folder"], u["label"], u.get("cue_file_id"), m["id"]))
        if m["cue_file_id"] != u.get("cue_file_id"):
            # same image, another cue sheet (a moved sheet keeps its file row): its track layout
            # may differ from the one the decision was made on -- keep the decision, but review
            _flag(con, m["id"], CUE_REPLACED)
        c["moved"] += 1
    if scope is not None:
        keep = {u["key"] for u in units}
        for row in con.execute("SELECT id, key, folder FROM unit WHERE root_id=?", (root_id,)).fetchall():
            in_scope = not scope or row["folder"] == scope or row["folder"].startswith(scope + "/")
            if in_scope and row["key"] not in keep:
                con.execute("DELETE FROM decision WHERE unit_id=?", (row["id"],))
                con.execute("DELETE FROM unit WHERE id=?", (row["id"],))
                c["removed_stale"] += 1
    for u in units:
        row = con.execute("SELECT id, status, file_ids FROM unit WHERE root_id=? AND key=?",
                          (root_id, u["key"])).fetchone()
        ids = json.dumps(sorted(u["file_ids"]))
        if row is None:
            con.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status, queued_at, cue_file_id) "
                        "VALUES(?,?,?,?,?,?, 'queued', ?, ?)",
                        (root_id, u["folder"], u["label"], u["kind"], u["key"], ids, now, u.get("cue_file_id")))
            c["queued"] += 1
        elif requeue or row["file_ids"] != ids or row["status"] == "error":
            con.execute("UPDATE unit SET status='queued', file_ids=?, label=?, queued_at=?, error=NULL "
                        "WHERE id=?", (ids, u["label"], now, row["id"]))
            if row["file_ids"] != ids:
                # different files -> a different album: the old decision no longer applies
                con.execute("DELETE FROM decision WHERE unit_id=?", (row["id"],))
            c["requeued"] += 1
        else:
            c["already_done" if row["status"] == "done" else "queued"] += 1
    con.commit()
    return c
