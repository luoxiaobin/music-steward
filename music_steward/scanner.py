"""Inventory scan: walk a root, record every file, probe audio, read tags.

Read-only on the collection. Guarantees:
  * no folder or file cap; file vs folder decided by the filesystem, never the extension
  * incremental: a file whose (NFC path, size, mtime_ns) is unchanged is not re-read
  * a file that vanishes mid-scan is skipped (and marked removed if it was known)
  * unreadable files are recorded with their error; the scan carries on
  * if the ROOT disappears (unplugged drive, dropped share) the scan aborts and
    marks NOTHING as removed -- known files stay as they were
  * folders that could not be listed keep their known files (not marked removed)
  * identity follows the content (ADR 0002): every file gets a content_id; a new path whose content
    is a known file that is no longer at its old path is that file, moved (it keeps its row, so its
    albums and decisions); a changed file whose content_id changed flags the decisions made on it
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .identity import identity
from .paths import ext_of, is_junk, kind_of, nfc
from .probe import ProbeError, probe
from .tags import read_tags


class RootUnavailable(Exception):
    pass


@dataclass
class ScanResult:
    scan_id: int
    root_id: int
    status: str
    counts: dict = field(default_factory=dict)
    abort_reason: str | None = None
    seconds: float = 0.0


@dataclass
class _Found:
    fs_rel: str          # as returned by the filesystem
    rel: str             # NFC key
    size: int
    mtime_ns: int
    kind: str
    ext: str


def _root_ok(root: str) -> bool:
    try:
        return os.path.isdir(root) and os.access(root, os.R_OK)
    except OSError:
        return False


def _ensure_root(con: sqlite3.Connection, root_abs: str) -> int:
    key = nfc(root_abs)
    row = con.execute("SELECT id FROM root WHERE path=?", (key,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute("INSERT INTO root(path, added_at) VALUES(?,?)", (key, time.time()))
    return cur.lastrowid


def _analyze(root: str, item: _Found) -> dict:
    """Runs in a worker thread: ffprobe + tags. Never writes anything."""
    path = os.path.join(root, item.fs_rel)
    out = {"probe_status": None}
    if item.kind != "audio":
        out["content_id"] = _content_id(path, item.kind, True)
        return out
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    try:
        p = probe(path, ext=item.ext)
        out.update(probe_status="ok", probe_error=None, content_note=None, **p.as_dict())
    except ProbeError as e:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        out.update(probe_status="error", probe_error=str(e), content_note=e.note)
    except Exception as e:  # timeouts, decode crashes: record, keep going
        out.update(probe_status="error", probe_error=f"{type(e).__name__}: {e}")
    try:
        t = read_tags(path)
        out.update(tag_format=t.tag_format, tags_json=json.dumps(t.values, ensure_ascii=False),
                   tag_issues_json=json.dumps(t.issues, ensure_ascii=False) if t.issues else None)
    except FileNotFoundError:
        raise
    except Exception as e:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        out.update(tag_format=None, tags_json=None,
                   tag_issues_json=json.dumps([{"kind": "tag_read_error", "error": str(e)[:300]}]))
    out["content_id"] = _content_id(path, item.kind, out["probe_status"] == "ok")
    return out


def _content_id(path: str, kind: str, probe_ok: bool) -> str:
    """'' = looked, none (unreadable audio, a large non-audio file): not looked at again."""
    try:
        return identity(path, kind, probe_ok) or ""
    except FileNotFoundError:
        raise
    except OSError:
        return ""


COMMIT_EVERY = 200     # files per transaction while scanning

PROPS = ("probe_status", "probe_error", "container", "codec", "duration", "duration_estimated",
         "bit_rate", "bit_rate_estimated", "sample_rate", "channels", "bits_per_sample",
         "audio_offset", "content_note",
         "tag_format", "tags_json", "tag_issues_json", "content_id")


def _write(con, row_id: int, f: _Found, vals: dict, scan_id: int, clear_hashes: bool):
    con.execute(
        "UPDATE file SET relpath=?, fs_relpath=?, kind=?, ext=?, size=?, mtime_ns=?, last_scan=?, removed_at=NULL, "
        + ", ".join(f"{p}=?" for p in PROPS) +
        (", file_sha256=NULL, audio_hash=NULL, audio_hash_method=NULL, hash_error=NULL, hashed_size=NULL, "
         "hashed_mtime_ns=NULL" if clear_hashes else "") + " WHERE id=?",
        (f.rel, f.fs_rel, f.kind, f.ext, f.size, f.mtime_ns, scan_id, *vals.values(), row_id))


def _insert(con, root_id: int, f: _Found, vals: dict, scan_id: int):
    con.execute(
        "INSERT INTO file(root_id, relpath, fs_relpath, kind, ext, size, mtime_ns, first_scan, last_scan, "
        + ", ".join(PROPS) + ") VALUES(" + ",".join("?" * (9 + len(PROPS))) + ")",
        (root_id, f.rel, f.fs_rel, f.kind, f.ext, f.size, f.mtime_ns, scan_id, scan_id, *vals.values()))


def _settle(con, root: str, root_id: int, scan_id: int, held: list, claimed: set[int], found_rels: set[str],
            c: dict):
    """Decide, by content, which known row each held path is (ADR 0002).

    A row is a file; it follows its content. A held path takes the row of a known file with the same
    content whose own path no longer holds it -- because that path is gone, or because it now holds
    other content (two album folders swapped, with the same file names in both: Codex review on #6).
    Otherwise a path keeps its own row (its content changed: the decisions on it are flagged) or gets a
    new one. A row whose path was taken and whose content is found nowhere is removed (and flagged).
    Written in one go: paths are freed first, since two rows may trade places."""
    from .recognize.units import flag_changed
    if not held:
        return
    own = {k["id"] for _f, _v, k in held if k}           # rows whose path now holds other content
    taken: set[int] = set()
    plan = []
    rest = []
    for f, v, k in sorted(held, key=lambda x: x[0].rel):
        donor = None
        for r in con.execute("SELECT id, relpath, fs_relpath FROM file WHERE root_id=? AND content_id=? ORDER BY id",
                             (root_id, v["content_id"])).fetchall():
            if r["id"] in taken or r["id"] in claimed or (k is not None and r["id"] == k["id"]):
                continue
            free = r["id"] in own or (r["relpath"] not in found_rels
                                      and not os.path.lexists(os.path.join(root, r["fs_relpath"])))
            if free and (donor is None or os.path.basename(r["fs_relpath"]) == os.path.basename(f.fs_rel)):
                donor = r["id"]
        if donor is not None:
            taken.add(donor)
            plan.append((f, v, k, donor))
        else:
            rest.append((f, v, k))
    for f, v, k in rest:
        if k is not None and k["id"] not in taken:
            taken.add(k["id"])
            plan.append((f, v, k, k["id"]))
        else:
            plan.append((f, v, k, None))
    lost = own - taken
    movers = [rid for f, v, k, rid in plan if rid is not None and (k is None or rid != k["id"])]
    for rid in movers:                                   # free the paths first
        con.execute("UPDATE file SET relpath=? WHERE id=?", (f"\x00moving/{rid}", rid))
    if lost:
        flag_changed(con, sorted(lost))
        for rid in lost:
            # its path now holds a file that came from elsewhere, and its own content is nowhere
            con.execute("UPDATE file SET relpath = relpath || ?, removed_at=? WHERE id=?",
                        (f" (replaced, scan {scan_id})", time.time(), rid))
        c["_lost"] = len(lost)
    for f, v, k, rid in plan:
        if rid is None:
            c["n_new"] += 1
            _insert(con, root_id, f, v, scan_id)
        elif k is not None and rid == k["id"]:
            c["n_changed"] += 1                          # same path, other content
            flag_changed(con, [rid])
            _write(con, rid, f, v, scan_id, clear_hashes=True)
        else:
            c["n_moved"] += 1                            # this row's file, now at this path
            claimed.add(rid)
            old = con.execute("SELECT size, mtime_ns FROM file WHERE id=?", (rid,)).fetchone()
            # a pure move keeps the bytes and so the saved hashes (hashing a NAS is slow); moved and
            # retagged: the whole-file hashes are stale
            _write(con, rid, f, v, scan_id, clear_hashes=(old["size"], old["mtime_ns"]) != (f.size, f.mtime_ns))


def resolve(con: sqlite3.Connection, path: str) -> tuple[str, str]:
    """(root, subpath): a path inside an already-known root is scanned as a sub-folder
    of that root, so its files keep their identity and nothing else is touched."""
    p = os.path.abspath(path)
    best = None
    for (r,) in con.execute("SELECT path FROM root"):
        if nfc(p) == r or nfc(p).startswith(r.rstrip("/") + "/"):
            if best is None or len(r) > len(best):
                best = r
    if best is None or nfc(p) == best:
        return p, ""
    sub = p[len(best.rstrip("/")) + 1:] if nfc(p[:len(best)]) == best else os.path.relpath(p, best)
    return best, sub.strip("/")


def scan(con: sqlite3.Connection, root_path: str, workers: int = 8, progress=None,
         reprobe_errors: bool = False, _hooks: dict | None = None) -> ScanResult:
    """Scan root_path. If it lies inside a known root, only that sub-folder is scanned
    and only files under it can be marked removed."""
    hooks = _hooks or {}
    t0 = time.time()
    root, sub = resolve(con, root_path)
    if not _root_ok(root) or (sub and not os.path.isdir(os.path.join(root, sub))):
        raise RootUnavailable(f"not available: {os.path.join(root, sub)}")
    root_id = _ensure_root(con, root)
    subkey = nfc(sub)
    # a previous scan killed mid-way (Ctrl-C, crash) never reached 'complete'
    con.execute("UPDATE scan SET status='interrupted', finished_at=? WHERE root_id=? AND status='running'",
                (t0, root_id))
    scan_id = con.execute("INSERT INTO scan(root_id, started_at, status, subpath) VALUES(?,?, 'running', ?)",
                          (root_id, t0, subkey)).lastrowid
    con.commit()
    c = dict(n_files=0, n_new=0, n_changed=0, n_unchanged=0, n_removed=0, n_vanished=0,
             n_probe_errors=0, n_dir_errors=0, n_moved=0)

    def abort(reason: str) -> ScanResult:
        con.rollback()
        con.execute("UPDATE scan SET status='aborted', abort_reason=?, finished_at=? WHERE id=?",
                    (reason, time.time(), scan_id))
        con.commit()
        return ScanResult(scan_id, root_id, "aborted", c, reason, time.time() - t0)

    def in_scope(rel: str) -> bool:
        return not subkey or rel.startswith(subkey + "/")

    known = {r["relpath"]: r for r in con.execute(
        "SELECT id, relpath, size, mtime_ns, removed_at, probe_status, tag_issues_json, kind, fs_relpath, "
        "content_id FROM file WHERE root_id=?", (root_id,))
        if in_scope(r["relpath"])}

    # ---- 1. walk (listing only) ------------------------------------------
    found: list[_Found] = []
    failed_dirs: list[str] = []
    stack = [sub]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(os.path.join(root, d) if d else root) as it:
                entries = list(it)
        except OSError as e:
            if not _root_ok(root):
                return abort(f"root disappeared while listing '{d}': {e}")
            failed_dirs.append(nfc(d))
            c["n_dir_errors"] += 1
            con.execute("INSERT OR REPLACE INTO dir_note VALUES(?,?,?,?,?)",
                        (root_id, nfc(d), "unlistable", str(e)[:300], scan_id))
            continue
        if "after_list" in hooks:
            hooks["after_list"](d, entries)
        for e in sorted(entries, key=lambda x: x.name):
            if is_junk(e.name):
                continue
            rel_fs = f"{d}/{e.name}" if d else e.name
            try:
                if e.is_symlink():
                    continue  # never follow links out of the root
                if e.is_dir(follow_symlinks=False):
                    if kind_of(e.name) == "audio":
                        con.execute("INSERT OR REPLACE INTO dir_note VALUES(?,?,?,?,?)",
                                    (root_id, nfc(rel_fs), "dir_named_like_file", None, scan_id))
                    stack.append(rel_fs)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except FileNotFoundError:
                c["n_vanished"] += 1
                continue
            except OSError:
                if not _root_ok(root):
                    return abort(f"root disappeared while reading '{rel_fs}'")
                c["n_vanished"] += 1
                continue
            found.append(_Found(rel_fs, nfc(rel_fs), st.st_size, st.st_mtime_ns,
                                kind_of(e.name), ext_of(e.name)))

    if "after_walk" in hooks:
        hooks["after_walk"](found)

    # ---- 2. classify: unchanged vs new/changed --------------------------
    seen: set[str] = set()
    todo: list[_Found] = []
    backfill: list[tuple[int, str, str, bool]] = []    # known files without a content_id yet
    for f in found:
        k = known.get(f.rel)
        retry = reprobe_errors and k is not None and (
            k["probe_status"] == "error" or '"tag_read_error"' in (k["tag_issues_json"] or ""))
        if k and not retry and k["removed_at"] is None and k["size"] == f.size and k["mtime_ns"] == f.mtime_ns:
            seen.add(f.rel)
            c["n_unchanged"] += 1
            if k["content_id"] is None:
                backfill.append((k["id"], f.fs_rel, k["kind"], k["probe_status"] == "ok"))
        else:
            todo.append(f)
    if seen:
        con.executemany("UPDATE file SET last_scan=? WHERE root_id=? AND relpath=?",
                        [(scan_id, root_id, r) for r in seen])

    # ---- 3. analyze new/changed files in parallel; write in this thread --
    done = 0
    claimed: set[int] = set()          # rows taken over by a moved file in this scan
    found_rels = {f.rel for f in found}
    held: list = []                    # (found, values, known row or None): settled after the reads
    total = len(todo) + len(backfill)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        # identities for files recorded before identities existed (one-time, ADR 0002)
        bfuts = {pool.submit(_content_id, os.path.join(root, fs), kind, ok): fid for fid, fs, kind, ok in backfill}
        for fut in as_completed(bfuts):
            done += 1
            try:
                con.execute("UPDATE file SET content_id=? WHERE id=?", (fut.result(), bfuts[fut]))
            except FileNotFoundError:
                if not _root_ok(root):
                    pool.shutdown(cancel_futures=True)
                    return abort("root disappeared while reading files")
            if done % COMMIT_EVERY == 0:
                con.commit()
                if progress:
                    progress(done, total)
        futs = {pool.submit(_analyze, root, f): f for f in todo}
        for fut in as_completed(futs):
            f = futs[fut]
            done += 1
            try:
                res = fut.result()
            except FileNotFoundError:
                if not _root_ok(root):
                    pool.shutdown(cancel_futures=True)
                    return abort("root disappeared while reading files")
                c["n_vanished"] += 1
                continue
            vals = {p: res.get(p) for p in PROPS}
            for b in ("duration_estimated", "bit_rate_estimated"):
                if vals[b] is not None:
                    vals[b] = int(bool(vals[b]))
            if vals["probe_status"] == "error":
                c["n_probe_errors"] += 1
            k = known.get(f.rel)
            cid = vals["content_id"]
            seen.add(f.rel)
            if cid and ((k and k["content_id"] != cid) or (not k and con.execute(
                    "SELECT 1 FROM file WHERE root_id=? AND content_id=? LIMIT 1", (root_id, cid)).fetchone())):
                # a path that now holds other content, or a new path holding known content: it may be a
                # file that moved (folders swapped, renamed, a file moved in) -- decided once every file
                # has been read (_settle). Nothing is written for it before then, so an interrupted scan
                # simply sees it as changed again next time
                held.append((f, vals, k))
            elif k:
                # a row marked removed whose file is back with the very same size and time (files set
                # aside by retire.py and restored): the bytes are the same, so its saved hashes stay valid
                back = k["removed_at"] is not None and (k["size"], k["mtime_ns"]) == (f.size, f.mtime_ns)
                c["n_unchanged" if back else "n_changed"] += 1
                if k["content_id"] != cid or not cid:
                    from .recognize.units import flag_changed
                    flag_changed(con, [k["id"]])     # same transaction as the new content_id below
                _write(con, k["id"], f, vals, scan_id, clear_hashes=not back)
            else:
                c["n_new"] += 1
                _insert(con, root_id, f, vals, scan_id)
            if done % COMMIT_EVERY == 0:
                con.commit()
                if progress:
                    progress(done, total)
    except KeyboardInterrupt:
        # Ctrl-C: drop queued reads, keep what is already recorded, mark nothing removed.
        pool.shutdown(wait=False, cancel_futures=True)
        con.commit()
        con.execute("UPDATE scan SET status='interrupted', finished_at=? WHERE id=?", (time.time(), scan_id))
        con.commit()
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if progress:
        progress(done, total)
    _settle(con, root, root_id, scan_id, held, claimed, found_rels, c)

    # ---- 4. removals: only after a complete walk, never under failed dirs --
    if not _root_ok(root):
        return abort("root disappeared before the scan could finish")

    def under_failed(rel: str) -> bool:
        return any(fd == "" or rel.startswith(fd + "/") for fd in failed_dirs)

    gone = [k["id"] for rel, k in known.items()
            if rel not in seen and k["removed_at"] is None and not under_failed(rel) and k["id"] not in claimed]
    now = time.time()
    con.executemany("UPDATE file SET removed_at=? WHERE id=?", [(now, i) for i in gone])
    c["n_removed"] = len(gone) + c.pop("_lost", 0)
    for (rel,) in con.execute("SELECT relpath FROM dir_note WHERE root_id=? AND last_scan<>?",
                              (root_id, scan_id)).fetchall():
        if rel == subkey or in_scope(rel):
            con.execute("DELETE FROM dir_note WHERE root_id=? AND relpath=? AND last_scan<>?",
                        (root_id, rel, scan_id))
    c["n_files"] = len(seen)
    con.execute("UPDATE scan SET status='complete', finished_at=?, " +
                ", ".join(f"{k}=?" for k in c) + " WHERE id=?",
                (time.time(), *c.values(), scan_id))
    con.commit()
    from .collection import remember_share     # which network share this is, for the not-connected message
    remember_share(con, root_id, root)
    return ScanResult(scan_id, root_id, "complete", c, None, time.time() - t0)
