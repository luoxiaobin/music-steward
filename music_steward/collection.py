"""Collection folders (ADR 0004, phase 1): is a collection folder connected, and *Change path* -- the same
collection at a new path, e.g. a share mounted as /Volumes/music-1 instead of /Volumes/music.

Changing the path rewrites ONE thing: the collection folder's path in the app database. Files, albums,
decisions, plans and history stay attached to the folder's id; the next scan re-reads only files whose size
or time differs. Paths that other records hold in full (plans, apply journals, batches of parked originals)
are never rewritten -- journals are append-only -- so the change is refused while one of them is still
needed (an unfinished copy run, originals still parked), and a plan made before the change is refused at
Apply (apply/runner.py: `sources_moved`).

Nothing here reads or writes inside a collection folder except `os.lstat` of sample files at the NEW path
(the same-collection check) and listing the mounted shares. Messages are fixed sentences (translated by the
interface); paths come separately in `detail`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import time

from . import folders, settings
from .paths import nfc

SAMPLE = 200          # files compared at the new path
NEEDED = 0.90         # share of them that must be there with the same size; there is no override (ADR 0004 §8)

MESSAGES = {
    "not_connected": "This collection folder is not connected. If the share is now mounted somewhere else, use "
                     "Change path… below. Otherwise connect it (Finder → Go → Connect to Server) and press Check again.",
    "share_elsewhere": "The same share is now at this path. Change path to it?",
    "accepted": "Checked {a} files: {b} are at the new path with the same size. Your albums and decisions stay. "
                "The next scan updates the inventory.",
    "accepted_empty": "No files of this collection folder are known yet, so there was nothing to compare. "
                      "The next scan reads the new path.",
    "refused": "Only {b} of {a} files were found at that path, so this does not look like the same collection. "
               "To use it as another collection folder, add it instead.",
    "already_registered": "That folder is already a collection folder of its own, with its own albums and decisions.",
    "overlaps": "That folder is inside, or contains, another collection folder:",
    "same_path": "That is already the path of this collection folder.",
    "missing": "That folder does not exist.",
    "job_running": "Wait until the running job has finished:",
    "unfinished_run": "A copy is unfinished. Finish it (Resume) or undo it first. Run:",
    "parked": "Originals are still set aside in _ORIGINALS. Restore or delete them first.",
    "done": "Path changed. Retire originals is off for this folder again. Scan now?",
    "unknown": "No such collection folder.",
}


class CollectionError(ValueError):
    pass


def _msg(code: str, detail: str | None = None, **counts) -> dict:
    text = MESSAGES[code]
    for k, v in counts.items():
        text = text.replace("{" + k + "}", str(v))
    return {"code": code, "message": text, "detail": detail}


def connected(path: str) -> bool:
    try:
        return os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return False


def _row(con: sqlite3.Connection, root_id: int) -> sqlite3.Row:
    r = con.execute("SELECT * FROM root WHERE id=?", (root_id,)).fetchone()
    if r is None:
        raise CollectionError(MESSAGES["unknown"])
    return r


# ---- share identity (ADR 0004 §12) -------------------------------------------------------------------------
def remember_share(con: sqlite3.Connection, root_id: int, path: str) -> None:
    """When a collection folder lives on a mounted network share, keep its host, share name and the folder
    inside the share -- never a user name or password -- so a new mount point of the same share can be
    suggested when the old one is gone. Best effort: a Mac that cannot list its mounts just suggests nothing."""
    real = os.path.realpath(path)
    best = None
    for host, share, mount in folders._mounted_shares():
        m = os.path.realpath(mount)
        if (real == m or real.startswith(m.rstrip("/") + "/")) and (best is None or len(m) > len(best[2])):
            best = (host, share, m)
    if best is None:
        return
    sub = os.path.relpath(real, best[2])
    con.execute("UPDATE root SET share_host=?, share_name=?, share_sub=? WHERE id=?",
                (best[0], best[1], "" if sub == "." else sub, root_id))
    con.commit()


def suggestion(con: sqlite3.Connection, root_id: int) -> str | None:
    """Where the same share is mounted now, if the collection folder is not connected and it is."""
    r = _row(con, root_id)
    if not r["share_host"] or connected(r["path"]):
        return None
    for host, share, mount in folders._mounted_shares():
        if host == r["share_host"] and share == r["share_name"]:
            p = os.path.join(mount, r["share_sub"]) if r["share_sub"] else mount
            if os.path.normpath(p) != os.path.normpath(r["path"]) and connected(p):
                return p
    return None


def describe(con: sqlite3.Connection, root_id: int) -> dict:
    r = _row(con, root_id)
    ok = connected(r["path"])
    return {"id": r["id"], "path": r["path"], "connected": ok,
            "message": None if ok else MESSAGES["not_connected"],
            "suggestion": None if ok else suggestion(con, root_id),
            "retire_allowed": settings.retire_allowed(con, r["id"]),
            "history": history(con, r["id"])}


def history(con: sqlite3.Connection, root_id: int) -> list[dict]:
    """The path changes of this collection folder, oldest first."""
    out = []
    for h in con.execute("SELECT at, detail_json FROM event WHERE root_id=? AND kind='path_changed' ORDER BY id",
                         (root_id,)):
        d = json.loads(h["detail_json"] or "{}")
        out.append({"at": h["at"], "old_path": d.get("old_path"), "new_path": d.get("new_path"),
                    "checked": d.get("checked"), "matched": d.get("matched")})
    return out


# ---- what blocks a change ------------------------------------------------------------------------------------
def _unfinished_runs(con: sqlite3.Connection, root_id: int) -> list[int]:
    """Copy runs of this collection folder that are not done: interrupted (never finished), interrupted while
    being planned, or stopped with files still to copy. Their journals hold the old paths, and Resume needs
    them. Failed files do not count: Retry of an old run is refused after the change (runner.sources_moved)."""
    from .apply.protocol import TERMINAL
    out = []
    for r in con.execute("SELECT run.id, run.finished_at, run.summary_json FROM run JOIN plan p ON p.id=run.plan_id "
                         "WHERE p.root_id=? AND run.undone_at IS NULL ORDER BY run.id", (root_id,)):
        s = json.loads(r["summary_json"] or "{}")
        if r["finished_at"] is None or not s.get("complete_plan", True) or \
                any(st not in TERMINAL for st in (s.get("states") or {})):
            out.append(r["id"])
    return out


def _parked(con: sqlite3.Connection, root_id: int) -> bool:
    return con.execute(
        "SELECT 1 FROM retired_file rf JOIN retire_batch b ON b.id=rf.batch_id WHERE b.root_id=? AND "
        "(rf.state NOT IN ('restored','deleted') OR b.status='running') LIMIT 1", (root_id,)).fetchone() is not None


def blockers(con: sqlite3.Connection, root_id: int, job: dict | None = None) -> list[dict]:
    """Reasons the path cannot change right now (ADR 0004 §9). `job`: the app's running job, if any."""
    out = []
    if job and job.get("running"):
        out.append(_msg("job_running", job.get("kind")))
    runs = _unfinished_runs(con, root_id)
    if runs:
        out.append(_msg("unfinished_run", ", ".join(f"#{n}" for n in runs)))
    if _parked(con, root_id):
        out.append(_msg("parked"))
    return out


# ---- the same-collection check -------------------------------------------------------------------------------
def sample(con: sqlite3.Connection, root_id: int, new_path: str, n: int = SAMPLE) -> tuple[int, int]:
    """(checked, matched): up to n known files, spread evenly over the inventory, looked up at the same
    relative path under new_path with the same size. Only lstat: nothing is opened; a link does not count."""
    rows = con.execute("SELECT fs_relpath, size FROM file WHERE root_id=? AND removed_at IS NULL ORDER BY id",
                       (root_id,)).fetchall()
    if len(rows) > n:
        step = len(rows) / n
        rows = [rows[int(i * step)] for i in range(n)]
    matched = 0
    for r in rows:
        try:
            st = os.lstat(os.path.join(new_path, r["fs_relpath"]))
        except OSError:
            continue
        matched += stat.S_ISREG(st.st_mode) and st.st_size == r["size"]
    return len(rows), matched


def _inside(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/")


def check(con: sqlite3.Connection, root_id: int, text: str, job: dict | None = None) -> dict:
    """Everything Change path would check, changing nothing. {ok, path, result, blockers, checked, matched}."""
    r = _row(con, root_id)
    out = {"ok": False, "root_id": root_id, "old_path": r["path"], "path": None, "result": None,
           "blockers": blockers(con, root_id, job), "checked": 0, "matched": 0}
    fc = folders.check_folder(con, text, "any")
    if fc["level"] == "empty":
        return out
    if fc["level"] == "error":
        out["result"] = {"code": "folder", "message": fc["message"], "detail": fc.get("detail"),
                         "suggestion": fc.get("suggestion")}
        return out
    if not fc["exists"]:
        out["result"] = _msg("missing")
        return out
    new = nfc(os.path.abspath(os.path.expanduser(text.strip().strip("\"'").strip())))
    out["path"] = new
    new_real = os.path.realpath(new)
    if new == r["path"]:
        out["result"] = _msg("same_path")
        return out
    for o in con.execute("SELECT id, path FROM root WHERE id<>?", (root_id,)):
        if new == o["path"] or new_real == os.path.realpath(o["path"]):
            out["result"] = _msg("already_registered", o["path"])
            return out
        o_real = os.path.realpath(o["path"])
        if _inside(new_real, o_real) or _inside(o_real, new_real):
            out["result"] = _msg("overlaps", o["path"])
            return out
    checked, matched = sample(con, root_id, new)
    out.update(checked=checked, matched=matched)
    if checked and matched < NEEDED * checked:
        out["result"] = _msg("refused", a=checked, b=matched)
        return out
    out["result"] = _msg("accepted", a=checked, b=matched) if checked else _msg("accepted_empty")
    out["ok"] = not out["blockers"]
    return out


def set_path(con: sqlite3.Connection, root_id: int, text: str, job: dict | None = None) -> dict:
    """Change the path of a collection folder, after every check again. Retire originals is switched off for
    it (the owner allowed it for the old place), and a history entry records what was done and why."""
    c = check(con, root_id, text, job)
    if not c["ok"]:
        why = c["blockers"][0] if c["blockers"] else c["result"]
        raise CollectionError(why["message"] + (f" {why['detail']}" if why and why.get("detail") else "")
                              if why else "choose the new folder")
    con.execute("UPDATE root SET path=?, share_host=NULL, share_name=NULL, share_sub=NULL WHERE id=?",
                (c["path"], root_id))
    con.execute("INSERT INTO event(at, kind, root_id, detail_json) VALUES(?, 'path_changed', ?, ?)",
                (time.time(), root_id, json.dumps({"old_path": c["old_path"], "new_path": c["path"],
                                                   "checked": c["checked"], "matched": c["matched"]},
                                                  ensure_ascii=False)))
    con.execute("INSERT OR REPLACE INTO meta VALUES(?, 'off')", (f"setting:retire_root:{int(root_id)}",))
    con.commit()
    remember_share(con, root_id, c["path"])
    return {**c, "changed": True, "done": _msg("done")}
