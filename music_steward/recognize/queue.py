"""Resumable recognition queue: units run one at a time, each in its own worker process.

One at a time also keeps us inside MusicBrainz's ~1 request/second limit.
An interrupted run leaves units 'queued' or 'running'; the next run picks them up.
"""
from __future__ import annotations

import sqlite3
import subprocess
import threading
import time

UNIT_TIMEOUT = 20 * 60


def pending(con: sqlite3.Connection, root_id: int | None = None, folder: str = "") -> list[int]:
    q = "SELECT id FROM unit WHERE status IN ('queued','running')"
    args: tuple = ()
    if root_id is not None:
        q += " AND root_id=?"
        args += (root_id,)
    if folder:
        q += " AND (folder=? OR (folder >= ? AND folder < ?))"
        args += (folder, folder + "/", folder + "/￿")
    return [r[0] for r in con.execute(q + " ORDER BY id", args)]


def _record_crash(db_path, uid, msg: str):
    """A worker that died before recording its own error (crash, lock, kill) must not leave
    its album 'queued' or 'running' with no explanation."""
    from .. import db
    con = db.connect(db_path)
    try:
        con.execute("UPDATE unit SET status='error', error=?, finished_at=? WHERE id=? AND status IN ('queued','running')",
                    (msg, time.time(), uid))
        con.commit()
    finally:
        con.close()


def run(db_path: str, unit_ids: list[int], progress=None, stop: threading.Event | None = None) -> dict:
    c = {"done": 0, "errors": 0, "seconds": 0.0}
    t0 = time.time()
    for i, uid in enumerate(unit_ids, 1):
        if stop is not None and stop.is_set():
            break
        try:
            from ..environment import worker_cmd      # `python -m` here; the app binary itself in the packaged app
            r = subprocess.run(worker_cmd(["--db", str(db_path), "--unit", str(uid)]),
                               capture_output=True, timeout=UNIT_TIMEOUT)
            c["done" if r.returncode == 0 else "errors"] += 1
            if r.returncode != 0:
                _record_crash(db_path, uid, f"worker exited with code {r.returncode}: "
                              + r.stderr.decode(errors="replace")[-1500:])
        except subprocess.TimeoutExpired:
            from .. import db
            con = db.connect(db_path)
            con.execute("UPDATE unit SET status='error', error='timed out after 20 minutes' WHERE id=?", (uid,))
            con.commit()
            con.close()
            c["errors"] += 1
        if progress:
            progress(i, len(unit_ids))
    c["seconds"] = round(time.time() - t0, 1)
    return c
