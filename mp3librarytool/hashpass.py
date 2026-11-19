"""Lazy, resumable hashing of audio files already in the inventory.

Separate from the scan because it reads every byte (slow over SMB). Any file
whose size/mtime differ from the inventory is skipped (re-scan first), and a
file that changes while being hashed gets no hash.
"""
from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .hashing import HashError, audio_hash, file_sha256


def pending(con: sqlite3.Connection, root_id: int | None = None):
    q = ("SELECT f.id, f.fs_relpath, f.ext, f.size, f.mtime_ns, r.path AS root FROM file f "
         "JOIN root r ON r.id=f.root_id WHERE f.removed_at IS NULL AND f.kind='audio' "
         "AND f.probe_status='ok' AND (f.file_sha256 IS NULL OR f.hashed_size<>f.size "
         "OR f.hashed_mtime_ns<>f.mtime_ns) AND f.hash_error IS NULL")
    args = ()
    if root_id is not None:
        q += " AND f.root_id=?"
        args = (root_id,)
    return con.execute(q + " ORDER BY f.id", args).fetchall()


def _one(row) -> dict:
    path = os.path.join(row["root"], row["fs_relpath"])
    st = os.stat(path)
    if st.st_size != row["size"] or st.st_mtime_ns != row["mtime_ns"]:
        return {"skip": "changed since scan (re-scan first)"}
    sha = file_sha256(path)
    ah, method = audio_hash(path, row["ext"])
    st2 = os.stat(path)
    if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
        return {"skip": "changed while hashing"}
    return {"sha": sha, "ah": ah, "method": method, "size": st.st_size, "mtime": st.st_mtime_ns}


def run(con: sqlite3.Connection, root_id: int | None = None, workers: int = 4,
        limit: int | None = None, progress=None) -> dict:
    rows = pending(con, root_id)
    if limit:
        rows = rows[:limit]
    c = {"hashed": 0, "skipped": 0, "errors": 0, "bytes": 0}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_one, r): r for r in rows}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                res = fut.result()
            except (HashError, OSError) as e:
                c["errors"] += 1
                con.execute("UPDATE file SET hash_error=? WHERE id=?", (str(e)[:500], r["id"]))
                continue
            if "skip" in res:
                c["skipped"] += 1
                continue
            con.execute("UPDATE file SET file_sha256=?, audio_hash=?, audio_hash_method=?, "
                        "hashed_size=?, hashed_mtime_ns=?, hash_error=NULL WHERE id=?",
                        (res["sha"], res["ah"], res["method"], res["size"], res["mtime"], r["id"]))
            c["hashed"] += 1
            c["bytes"] += res["size"]
            if i % 50 == 0:
                con.commit()
                if progress:
                    progress(i, len(rows))
    con.commit()
    c["seconds"] = round(time.time() - t0, 1)
    c["remaining"] = len(pending(con, root_id))
    return c
