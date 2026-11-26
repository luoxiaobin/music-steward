"""History: one readable list of what changed originals, copies or a collection folder's path (docs/LIFECYCLES.md).

Each line has a time, a kind, the collection folder and the albums it touched. It is written *after* an action has
finished, from what the action reported -- never in its way: the journals stay the truth for crash recovery, and a
history line that cannot be written never fails the action. Album names are looked up when the history is read,
so an album renamed by *Edit fields* shows its current name; a removed album shows the name kept with the line.

Messages are fixed sentences with numbers (translated by the interface); paths and album names come separately.
"""
from __future__ import annotations

import json
import sqlite3
import time

MESSAGES = {
    "path_changed": "The collection folder's path changed",
    "run_applied": "Copied {n} files (run #{id})",
    "run_resumed": "Resumed run #{id}: {n} files copied in all",
    "run_retried": "Retried the failed files of run #{id}: {n} files copied in all",
    "run_undone": "Undid run #{id}: {n} copies removed",
    "retired": "Set aside the originals of {n} albums ({m} files)",
    "restored": "Restored {n} original files",
    "archived": "Archived {n} parked original files (verified)",
    "archive_stopped": "Archive stopped: {n} parked original files copied so far",
    "deleted": "Deleted {n} parked original files",
    "albums_removed": "Removed {n} not-found albums from the app (no file touched)",
}


def _text(kind: str, **nums) -> str:
    t = MESSAGES[kind]
    for k, v in nums.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def record(con: sqlite3.Connection, kind: str, root_id: int | None, units=(), run_id: int | None = None,
           batch_id: int | None = None, detail: dict | None = None, labels: dict | None = None) -> int | None:
    """Add one line. Best effort: the action it describes has already happened. Each album's name is kept with
    the line (labels: for albums whose record is already gone), so the History can name a removed album."""
    try:
        eid = con.execute("INSERT INTO event(at, kind, root_id, run_id, batch_id, detail_json) VALUES(?,?,?,?,?,?)",
                          (time.time(), kind, root_id, run_id, batch_id,
                           json.dumps(detail or {}, ensure_ascii=False))).lastrowid
        rows = []
        for u in {int(u) for u in units if u is not None}:
            name = (labels or {}).get(u)
            if name is None:
                r = con.execute("SELECT label FROM unit WHERE id=?", (u,)).fetchone()
                name = r[0] if r else None
            rows.append((eid, u, name))
        con.executemany("INSERT OR IGNORE INTO event_unit(event_id, unit_id, label) VALUES(?,?,?)", rows)
        con.commit()
        return eid
    except sqlite3.Error:
        con.rollback()
        return None


def detach_units(con: sqlite3.Connection, ids) -> None:
    """Before an album's record is deleted: keep its History lines, with its name, under the negative id. SQLite
    may give a deleted row's id to the next new album, whose History must not inherit them. No commit here: the
    caller deletes the albums and commits in the same transaction."""
    for uid in ids:
        con.execute("UPDATE event_unit SET label=COALESCE(label, (SELECT label FROM unit WHERE id=?)) WHERE unit_id=?",
                    (uid, uid))
        con.execute("UPDATE event_unit SET unit_id=? WHERE unit_id=?", (-uid, uid))


def _run_root_units(con, run_id: int) -> tuple[int | None, list[int]]:
    row = con.execute("SELECT p.root_id, p.id FROM run JOIN plan p ON p.id=run.plan_id WHERE run.id=?",
                      (run_id,)).fetchone()
    if row is None:
        return None, []
    return row["root_id"], [r[0] for r in con.execute(
        "SELECT DISTINCT unit_id FROM plan_item WHERE plan_id=? AND status='ok' AND unit_id IS NOT NULL", (row["id"],))]


def _batch_root_units(con, batch_id: int, states: tuple[str, ...]) -> tuple[int | None, list[int]]:
    row = con.execute("SELECT root_id FROM retire_batch WHERE id=?", (batch_id,)).fetchone()
    marks = ",".join("?" * len(states))
    return (row["root_id"] if row else None,
            [r[0] for r in con.execute(f"SELECT DISTINCT unit_id FROM retired_file WHERE batch_id=? AND state IN ({marks}) "
                                       "AND unit_id IS NOT NULL", (batch_id, *states))])


def after_job(con: sqlite3.Connection, kind: str, n: int, res: dict) -> None:
    """Record a finished copy run, undo, or action on parked originals, from the result it returned.
    Nothing is recorded for an error, or for an action that changed nothing."""
    if not isinstance(res, dict) or res.get("error") or res.get("status") == "error":
        return
    if kind in ("apply", "resume", "retry"):
        run_id = res.get("run")
        states = res.get("states") or {}
        copied = states.get("committed", 0)
        if run_id is None or (kind == "apply" and not states):
            return
        root_id, units = _run_root_units(con, run_id)
        ev = {"apply": "run_applied", "resume": "run_resumed", "retry": "run_retried"}[kind]
        record(con, ev, root_id, units, run_id=run_id, detail={"n": copied, "id": run_id, "states": states})
    elif kind == "undo":
        removed = (res.get("actions") or {}).get("removed", 0)
        root_id, units = _run_root_units(con, n)
        record(con, "run_undone", root_id, units, run_id=n,
               detail={"n": removed, "id": n, "actions": res.get("actions") or {}})
    elif kind in ("retire", "retire_resume"):
        if not res.get("batch"):
            return
        root_id, units = _batch_root_units(con, res["batch"], ("moved", "restored", "deleted"))
        record(con, "retired", root_id, units, batch_id=res["batch"],
               detail={"n": res.get("albums", 0), "m": res.get("files", 0), "bytes": res.get("bytes", 0),
                       "folder": res.get("folder"), "status": res.get("status")})
    elif kind == "restore":
        if not res.get("restored"):
            return
        root_id, units = _batch_root_units(con, n, ("restored",))
        record(con, "restored", root_id, units, batch_id=n, detail={"n": res["restored"]})
    elif kind == "archive":
        if not res.get("files"):
            return
        root_id, units = _batch_root_units(con, n, ("moved", "restored", "deleted"))
        record(con, "archived" if res.get("verified") else "archive_stopped", root_id, units, batch_id=n,
               detail={"n": res["files"], "dest": res.get("dest")})
    elif kind == "delete":
        if not res.get("deleted"):
            return
        root_id, units = _batch_root_units(con, n, ("deleted",))
        record(con, "deleted", root_id, units, batch_id=n, detail={"n": res["deleted"], "bytes": res.get("bytes", 0)})


def lines(con: sqlite3.Connection, root_id: int | None = None, unit_id: int | None = None, limit: int = 300) -> list[dict]:
    """Newest first. {id, at, kind, message, root, albums: [names], n_albums, path, old_path, dest, folder}."""
    q, args = "SELECT e.* FROM event e", []
    if unit_id is not None:
        q += " JOIN event_unit eu ON eu.event_id=e.id AND eu.unit_id=?"
        args.append(unit_id)
    if root_id is not None:
        q += " WHERE e.root_id=?"
        args.append(root_id)
    q += " ORDER BY e.id DESC LIMIT ?"
    args.append(limit)
    out = []
    for e in con.execute(q, args).fetchall():
        d = json.loads(e["detail_json"] or "{}")
        names = [r[0] for r in con.execute(      # the current name; the one kept with the line if the album is gone
            "SELECT COALESCE(u.label, eu.label) AS name FROM event_unit eu LEFT JOIN unit u ON u.id=eu.unit_id "
            "WHERE eu.event_id=? AND COALESCE(u.label, eu.label) IS NOT NULL ORDER BY name LIMIT 6", (e["id"],))]
        n_albums = con.execute("SELECT count(*) FROM event_unit WHERE event_id=?", (e["id"],)).fetchone()[0]
        root = con.execute("SELECT path FROM root WHERE id=?", (e["root_id"],)).fetchone() if e["root_id"] else None
        out.append({"id": e["id"], "at": e["at"], "kind": e["kind"],
                    "message": _text(e["kind"], **{k: d[k] for k in ("n", "m", "id") if k in d}),
                    "root": root["path"] if root else None, "albums": names, "n_albums": n_albums,
                    "old_path": d.get("old_path"), "new_path": d.get("new_path"),
                    "dest": d.get("dest"), "folder": d.get("folder")})
    return out
