"""Retire the originals (ADR 0003): move the files of verified albums out of the way, reversibly.

This is the ONE place the "sources are read-only" rule bends, so it is fenced in:

  * off unless the user allowed it for that collection folder (settings.retire_allowed);
  * a MOVE inside the same collection folder, on the same volume, into
        <root>/_ORIGINALS/<batch>/<original relative path>
    (a rename: instant, atomic, every byte kept); nothing is deleted here;
  * only files of albums whose organized copy is VERIFIED, and the checks are made again for each
    album right before its files move:
      - every item the run planned for the album is committed (or was already there, identical),
        and the run was not undone;
      - the organized copy still exists and its AUDIO is unchanged (fingerprint equals the journal's,
        so a retagged copy still counts);
      - the original is unchanged since it was copied (size, mtime, content identity -- ADR 0002);
      - no path component is a symlink; everything stays inside the root and on one device;
      - split albums: every track of the image is verified and the tracks cover the whole image;
  * labelled: README - ORIGINALS.txt (in the interface language) and MANIFEST.csv are written into the
    batch folder before the first file of an album moves;
  * journal-first like apply: an intent record, the rename, a 'moved' record; Resume settles what a crash
    left; Restore moves files back and never overwrites.

Archive and Delete (the other two choices the user is offered) come later (ADR 0003, build order).
"""
from __future__ import annotations

import csv
import errno
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from . import identity as idmod
from . import settings
from .apply import protocol
from .hashing import copy_audio_fingerprint, file_sha256

ORIGINALS = "_ORIGINALS"           # the holding folder; the scanner ignores it (paths.JUNK_NAMES)
README = "README - ORIGINALS.txt"
MANIFEST = "MANIFEST.csv"
SOURCE_KINDS = ("audio", "split", "cue", "lrc", "image")      # items that copy (a part of) a source file
OK_STATES = ("committed", "already_existed")


class RetireError(Exception):
    pass


class _Skip(Exception):
    """This album cannot be retired; the message says why (shown to the user)."""


# ---- opt-in ---------------------------------------------------------------------------------------
def require_allowed(con: sqlite3.Connection, root_id: int) -> None:
    if not settings.retire_allowed(con, root_id):
        raise RetireError("retiring originals is not allowed for this collection folder "
                          "(Settings → Originals)")


# ---- the checks (shared by the dry run and the real thing) ----------------------------------------
def _stat_same(path: str, size: int, mtime_ns: int) -> None:
    try:
        st = os.stat(path)
    except OSError as e:
        raise _Skip(f"the original cannot be read ({e.strerror or e})") from e
    if (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
        raise _Skip("the original changed since it was copied")


def _identity_same(path: str, f: sqlite3.Row) -> None:
    """The content identity recorded by the scan (ADR 0002) still matches the file on disk."""
    if f["removed_at"] is not None:
        raise _Skip("the original is no longer in the inventory; scan the folder again")
    if f["content_id"] is None:
        raise _Skip("the original has no identity yet; scan its folder again first")
    if f["content_id"] == "":
        return                                        # a large non-audio file: size and time only
    try:
        now = idmod.identity(path, f["kind"], f["probe_status"] == "ok")
    except OSError as e:
        raise _Skip(f"the original cannot be read ({e.strerror or e})") from e
    if now != f["content_id"]:
        raise _Skip("the original's content changed since the scan")


def _no_links(root: str, rel: str) -> None:
    """No symlink on the way from the root to the file (the scanner never follows them either)."""
    cur = root
    for part in rel.split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise _Skip("a link on the path of the original; it is never moved")


def _fp(path: str, codec: str | None) -> str:
    try:
        return copy_audio_fingerprint(path, Path(path).suffix, codec)
    except Exception as e:      # noqa: BLE001 -- unreadable or undecodable: the copy cannot be vouched for
        raise _Skip(f"the organized copy cannot be read ({type(e).__name__})") from e


def _verify_copy(op: dict, item: dict, src: str | None = None) -> str | None:
    """The organized copy of one item is intact. Returns a note when it differs only in a way that does
    not block (a side file the user edited), else None; raises _Skip when it does not verify.
    src: where the original is now, when it has already been parked (resuming an album)."""
    final, kind = Path(op["final"]), item["kind"]
    state = op["history"][-1]
    if state not in OK_STATES:
        raise _Skip(f"not copied ({state})")
    if not final.is_file():
        raise _Skip("the organized copy is missing")
    gen = (op.get("tags") or {}).get("generated_text")
    sp = (op.get("tags") or {}).get("split")
    if kind in ("audio", "split"):
        if state == "committed" and op.get("src_audio"):
            if _fp(str(final), op.get("codec")) != op["src_audio"]:
                raise _Skip("the audio of the organized copy changed")
        else:       # already there when the run started: prove it equals the original now
            src = src or op["src"]
            if sp is not None:
                from .apply.split import SplitError, verify
                try:
                    ok = verify(src, str(final), sp["start"], sp["end"], sp["rate"], sp.get("bits"), sp["src_ext"])
                except SplitError as e:
                    raise _Skip(f"cannot compare the track with its image ({e})") from e
            else:
                ok = _fp(src, op.get("codec")) == _fp(str(final), op.get("codec"))
            if not ok:
                raise _Skip("the organized copy is not the same audio as the original")
        return None
    # side files: cue (rewritten), lyrics (copied or split), cover images
    if state == "committed":
        if op.get("final_sha") and file_sha256(str(final)) != op["final_sha"]:
            return "a side file of the copy was edited since it was copied"
        return None
    if gen is not None:
        import hashlib
        if file_sha256(str(final)) != hashlib.sha256(gen.encode("utf-8-sig")).hexdigest():
            raise _Skip("an existing file at the destination is not the planned one")
    elif file_sha256(str(final)) != file_sha256(src or op["src"]):
        raise _Skip("an existing file at the destination is not a copy of the original")
    return None


def _coverage(split_ops: list[dict]) -> None:
    """The tracks cut from one image are contiguous and cover all of it."""
    cuts = sorted(((o["tags"]["split"]["start"], o["tags"]["split"]["end"]) for o in split_ops),
                  key=lambda c: c[0])
    if not cuts or cuts[0][0] != 0:
        raise _Skip("the tracks do not start at the beginning of the image")
    for (_s, e), (s2, _e2) in zip(cuts, cuts[1:]):
        if e is None or abs(e - s2) > 1e-6:
            raise _Skip("the tracks do not follow each other without a gap")
    if cuts[-1][1] is not None:
        raise _Skip("the last track does not run to the end of the image")


class Run:
    """What a run did, ready for checking: its plan items, journal ops and collection folder."""

    def __init__(self, con: sqlite3.Connection, run_id: int):
        r = con.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
        if r is None:
            raise RetireError("no such run")
        if r["undone_at"]:
            raise RetireError("this run was undone: there are no organized copies to rely on")
        self.row = r
        self.id = run_id
        self.plan = con.execute("SELECT * FROM plan WHERE id=?", (r["plan_id"],)).fetchone()
        self.root_id = self.plan["root_id"]
        self.root = con.execute("SELECT path FROM root WHERE id=?", (self.root_id,)).fetchone()["path"]
        self.ops = protocol.Journal(Path(r["journal"])).ops()
        items = [dict(x) for x in con.execute("SELECT * FROM plan_item WHERE plan_id=? ORDER BY seq", (r["plan_id"],))]
        self.units: dict[int, list[dict]] = {}
        for it in items:
            it["tags"] = json.loads(it.pop("tags_json") or "{}")
            self.units.setdefault(it["unit_id"], []).append(it)
        self.items = items

    def op(self, item: dict) -> dict | None:
        return self.ops.get(f"i{item['seq']}")


def check_album(con: sqlite3.Connection, run: Run, unit_id: int, moved: dict[int, str] | None = None) -> dict:
    """Is this album retirable right now? {eligible, reason, files, flags, kind}. Never raises on an
    album's own problems: they are the 'reason'.
    moved: {file id: where it is now} for files of this album that are already parked -- when an album
    that was interrupted is resumed, everything else is checked again exactly as for a first time."""
    items = run.units[unit_id]
    unit = con.execute("SELECT label, folder FROM unit WHERE id=?", (unit_id,)).fetchone()
    out = {"unit_id": unit_id, "label": unit["label"] if unit else str(unit_id),
           "folder": unit["folder"] if unit else "", "eligible": False, "reason": None, "files": [],
           "bytes": 0, "flags": [], "kind": "album"}
    try:
        dup = [i for i in items if i["status"] == "duplicate_source"]
        if dup:
            out["kind"] = "duplicate"
            out["files"] = _check_duplicate(con, run, items, dup, out["flags"], moved or {})
        else:
            out["files"] = _check_album(con, run, items, out["flags"], moved or {})
        out["bytes"] = sum(f["size"] for f in out["files"])
        out["eligible"] = bool(out["files"])
        if not out["files"]:
            out["reason"] = "nothing in this album was copied"
    except _Skip as e:
        out["reason"], out["files"] = str(e), []
    except OSError as e:        # a share that went away in the middle: not this album's fault, not safe either
        out["reason"], out["files"] = f"cannot check ({e.strerror or e})", []
    return out


def _file_entry(con, run: Run, f: sqlite3.Row, size: int, mtime_ns: int, kind: str,
                parked: str | None = None) -> dict:
    if parked is not None:                       # already set aside: it must still be there, the same size
        try:
            ok = os.stat(parked).st_size == size
        except OSError:
            ok = False
        if not ok:
            raise _Skip("a file that was set aside is missing or changed")
        return {"file_id": f["id"], "relpath": f["fs_relpath"], "path": parked, "size": size, "mtime_ns": mtime_ns,
                "content_id": f["content_id"], "kind": kind, "already_moved": True}
    path = os.path.join(run.root, f["fs_relpath"])
    _no_links(run.root, f["fs_relpath"])
    _stat_same(path, size, mtime_ns)
    _identity_same(path, f)
    return {"file_id": f["id"], "relpath": f["fs_relpath"], "path": path, "size": size, "mtime_ns": mtime_ns,
            "content_id": f["content_id"], "kind": kind}


def _check_album(con, run: Run, items: list[dict], flags: list[str], moved: dict[int, str]) -> list[dict]:
    sources: dict[int, dict] = {}                # file_id -> {"items": [...], "ops": [...]}
    for it in items:
        if it["status"] != "ok":
            raise _Skip(f"part of the album was blocked in the plan ({it['status']})")
        op = run.op(it)
        if op is None:
            raise _Skip("part of the album was not copied (the run was stopped before it)")
        note = _verify_copy(op, it, src=moved.get(it["file_id"]))
        if note and note not in flags:
            flags.append(note)
        if it["kind"] in SOURCE_KINDS:
            s = sources.setdefault(it["file_id"], {"items": [], "ops": []})
            s["items"].append(it)
            s["ops"].append(op)
    files = []
    for fid, s in sources.items():
        f = con.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
        if f is None:
            raise _Skip("an original is no longer in the inventory")
        split_ops = [o for o, i in zip(s["ops"], s["items"]) if i["kind"] == "split"]
        if split_ops:
            _coverage(split_ops)
        op0 = s["ops"][0]
        files.append(_file_entry(con, run, f, op0["src_size"], op0["src_mtime_ns"], s["items"][0]["kind"],
                                 parked=moved.get(fid)))
    # an album split into tracks: its original cue sheet is not copied (it only defined the cuts), but
    # it belongs to the image that goes: it moves with it, if it is the very sheet the cuts came from
    cut_ops = [o for o in (run.op(i) for i in items) if o and (o.get("tags") or {}).get("split")]
    if cut_ops:
        cue = cut_ops[0]["tags"]["split"].get("cue")
        row = con.execute("SELECT cue_file_id FROM unit WHERE id=?", (items[0]["unit_id"],)).fetchone()
        f = con.execute("SELECT * FROM file WHERE id=?", (row["cue_file_id"],)).fetchone() if row and row[0] else None
        if cue and f is not None and f["id"] not in sources:
            files.append(_file_entry(con, run, f, cue["size"], cue["mtime_ns"], "cue", parked=moved.get(f["id"])))
    return files


def _check_duplicate(con, run: Run, items: list[dict], dup: list[dict], flags: list[str],
                     moved: dict[int, str]) -> list[dict]:
    """A byte-identical duplicate of an album that was copied once (plan: duplicate_source). Its files go
    only if each is byte-identical to the source of the file that was copied and verified."""
    from .apply.naming import collision_key
    twins = {}
    for it in run.items:
        if it["status"] == "ok" and it["kind"] in SOURCE_KINDS:
            twins.setdefault(collision_key(it["dest_rel"]), []).append(it)
    files = []
    for it in dup:
        if it["kind"] not in SOURCE_KINDS:
            continue                                  # generated files (album cue) have no source to move
        if it["kind"] == "split":
            raise _Skip("a duplicate of an album split into tracks is not retired automatically")
        twin = next((t for t in twins.get(collision_key(it["dest_rel"]), []) if t["unit_id"] != it["unit_id"]
                     and t["kind"] == it["kind"]), None)
        top = run.op(twin) if twin else None
        if top is None or top["history"][-1] not in OK_STATES:
            raise _Skip("the copy of this album that was kept is not verified")
        _verify_copy(top, twin)
        f = con.execute("SELECT * FROM file WHERE id=?", (it["file_id"],)).fetchone()
        if f is None:
            raise _Skip("an original is no longer in the inventory")
        e = _file_entry(con, run, f, it["src_size"], it["src_mtime_ns"], it["kind"], parked=moved.get(it["file_id"]))
        want = top.get("src_sha")
        if want is None or file_sha256(e["path"]) != want:
            raise _Skip("a file is not byte-identical to the one that was copied")
        files.append(e)
    flags.append("a duplicate of an album that was copied once")
    return files


def dry_run(con: sqlite3.Connection, run_id: int) -> dict:
    """What retiring would do for this run. Changes nothing (reads, and re-reads the copies)."""
    run = Run(con, run_id)
    albums = [check_album(con, run, uid) for uid in sorted(k for k in run.units if k is not None)]
    ok = [a for a in albums if a["eligible"]]
    return {"run": run_id, "root_id": run.root_id, "root": run.root,
            "allowed": settings.retire_allowed(con, run.root_id), "albums": albums,
            "albums_ok": len(ok), "files_ok": sum(len(a["files"]) for a in ok),
            "bytes_ok": sum(a["bytes"] for a in ok),
            "albums_skipped": len(albums) - len(ok)}


# ---- README and manifest --------------------------------------------------------------------------
def readme_text(lang: str, *, date: str, run_id: int, dest_root: str, albums: int | None = None) -> str:
    from .i18n import dictionary
    d = dictionary(lang)
    t = lambda s: d.get(s, s)                                              # noqa: E731
    lines = [t("ORIGINAL FILES — set aside by Music Steward"), "",
             t("These are the ORIGINAL files that Music Steward replaced with organized copies. "
               "Nothing in this folder has been changed."), "",
             f"{t('Date:')} {date}", f"{t('Run:')} #{run_id}", f"{t('Organized copies:')} {dest_root}", "",
             t("What you can do with them:"),
             t("1. RESTORE them: in Music Steward open Originals and press Restore, or move the files back "
               "by hand to the paths listed in MANIFEST.csv."),
             t("2. ARCHIVE them: copy this folder to a backup drive and keep it there."),
             t("3. DELETE them to save space — only after you have played the organized copies and have a "
               "backup of them. Deleting cannot be undone."), "",
             t("MANIFEST.csv lists every file: its original path, size, the organized copy and the album."), ""]
    return "\n".join(lines)


def _manifest_row(f: dict, album: dict, copies: dict[int, list[str]]) -> list:
    return [f["relpath"], f["size"], f["content_id"] or "", " | ".join(copies.get(f["file_id"], [])),
            album["label"], album["folder"], album["unit_id"], f["kind"]]


# ---- batches ----------------------------------------------------------------------------------------
def _inside(path: str, base: str) -> bool:
    p, b = os.path.realpath(path), os.path.realpath(base)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)


_NO_PRIMITIVE = (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS, errno.ENOTTY)
_RENAME_NOREPLACE = 1                                    # Linux renameat2 flag
_AT_FDCWD = -100


def _renameat2_excl(src: str, dst: str) -> None:
    """Linux: rename that fails instead of replacing (renameat2 + RENAME_NOREPLACE)."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "renameat2"):
        raise OSError(errno.ENOSYS, "renameat2 not available")
    if libc.renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), dst)


def _after_claim() -> None:
    """Hook between claiming a name and renaming onto it (tests inject the race here)."""


def _rename_noreplace(src: str, dst: str, claimed=None) -> str:
    """Move src to dst and never replace anything at dst -- the same ladder as apply's publish
    (AGENTS.md: RENAME_EXCL / link / claim-then-rename). Returns the method used.

      1. an atomic exclusive rename where the volume has one (APFS renamex_np, Linux renameat2);
      2. else a hard link (fails if dst exists) and then unlinking src;
      3. else claim-then-rename, as for NAS shares: create dst exclusively as an empty placeholder (fails if
         anything is there), check it is still ours, then rename onto it. Residual window, documented in
         RECOVERY.md: another program writing the very same name in the microseconds between the check and
         the rename. `claimed(ino)` lets the caller journal the placeholder so a crash can remove it.
    """
    for primitive in (protocol.rename_excl, _renameat2_excl):
        try:
            primitive(src, dst)
            return "exclusive-rename"
        except FileExistsError:
            raise
        except OSError as e:
            if e.errno not in _NO_PRIMITIVE:
                raise
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno not in _NO_PRIMITIVE + (errno.EPERM,):
            raise
    else:
        os.unlink(src)                                      # a crash before this leaves one inode, two names
        return "link"
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    ino = os.fstat(fd).st_ino
    os.close(fd)
    if claimed is not None:
        claimed(ino)
    try:
        _after_claim()
        st = os.lstat(dst)
        if st.st_ino != ino or st.st_size != 0 or not stat.S_ISREG(st.st_mode):
            raise FileExistsError(errno.EEXIST, "the claimed name was taken by someone else", dst)
        os.rename(src, dst)
    except FileExistsError:
        raise                                               # not ours any more: leave whatever is there
    except BaseException:
        _drop_placeholder(dst, ino)
        raise
    return "claim"


def _same_inode(a: str, b: str) -> bool:
    """Two names of ONE regular file (a hard link): both are regular files themselves -- a symlink is never
    followed (os.path.samefile would, and a link at the original place pointing to the parked file made
    recovery delete the parked original) -- on one device, one inode, with more than one name."""
    try:
        sa, sb = os.lstat(a), os.lstat(b)
    except OSError:
        return False
    return (stat.S_ISREG(sa.st_mode) and stat.S_ISREG(sb.st_mode) and (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
            and sa.st_nlink > 1)


def _is_ours(path: str, size: int, mtime_ns: int) -> bool:
    """A regular file (never a link) with the size and modification time recorded for the file we are
    moving -- a rename keeps both, so this tells our file from anything else that has the same name."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size == size and st.st_mtime_ns == mtime_ns


def _is_placeholder(path: str, ino: int | None) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return ino is not None and st.st_ino == ino and st.st_size == 0 and stat.S_ISREG(st.st_mode)


def _drop_placeholder(path: str, ino: int | None) -> None:
    """Remove our own empty placeholder (identified by its inode), never anything else."""
    if _is_placeholder(path, ino):
        os.unlink(path)


def _journal_dir(con: sqlite3.Connection) -> Path:
    """Journals live next to the database that refers to them (~/.music-steward/journal for the app)."""
    return Path(con.execute("PRAGMA database_list").fetchone()[2]).parent / "journal"


def _new_batch(con: sqlite3.Connection, run: Run) -> sqlite3.Row:
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(run.root, ORIGINALS)
    if os.path.islink(base):
        raise RetireError(f"{ORIGINALS} is a link; refusing to use it")
    os.makedirs(base, exist_ok=True)
    for n in range(1, 100):                                  # a batch folder is always new (exclusive mkdir)
        key = f"run{run.id}-{ts}" + ("" if n == 1 else f"-{n}")
        folder = os.path.join(base, key)
        try:
            os.mkdir(folder)
            break
        except FileExistsError:
            continue
    else:
        raise RetireError("could not make a new batch folder")
    if not _inside(folder, base) or os.stat(folder).st_dev != os.stat(run.root).st_dev:
        raise RetireError("the holding folder is not on the same volume as the collection")
    lang = _lang(con)
    with open(os.path.join(folder, README), "x", encoding="utf-8") as fh:
        fh.write(readme_text(lang, date=time.strftime("%Y-%m-%d %H:%M"), run_id=run.id,
                             dest_root=run.plan["dest_root"]))
        fh.flush()
        os.fsync(fh.fileno())
    with open(os.path.join(folder, MANIFEST), "x", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerow(["original_path", "size", "content_identity", "organized_copy", "album",
                                 "album_folder", "album_id", "kind"])
        fh.flush()
        os.fsync(fh.fileno())
    jpath = _journal_dir(con) / f"retire-{key}.jsonl"
    bid = con.execute("INSERT INTO retire_batch(root_id, run_id, key, folder, journal, created_at, status) "
                      "VALUES(?,?,?,?,?,?, 'running')",
                      (run.root_id, run.id, key, folder, str(jpath), time.time())).lastrowid
    con.commit()
    return con.execute("SELECT * FROM retire_batch WHERE id=?", (bid,)).fetchone()


def _lang(con) -> str:
    v = settings.get(con, "language")
    return {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant", "fr-ca": "fr-CA", "es-us": "es-US"}.get(v, "en")


def _copies_of(run: Run, album: dict) -> dict[int, list[str]]:
    """{original file id: the organized copies made from it} (relative to the destination), for the
    manifest. A duplicate album's files point at the kept twin's copies; an original cue sheet of an
    album split into tracks (not copied itself) points at the album's generated cue sheet."""
    from .apply.naming import collision_key
    out: dict[int, list[str]] = {}
    items = run.units[album["unit_id"]]
    for it in items:
        op = run.op(it)
        if it["kind"] in SOURCE_KINDS:
            if op:
                out.setdefault(it["file_id"], []).append(os.path.relpath(op["final"], run.plan["dest_root"]))
            elif it["status"] == "duplicate_source":
                for t in run.items:
                    if (t["status"] == "ok" and t["unit_id"] != it["unit_id"] and t["kind"] == it["kind"]
                            and collision_key(t["dest_rel"]) == collision_key(it["dest_rel"])):
                        out.setdefault(it["file_id"], []).append(t["dest_rel"])
    generated = [it["dest_rel"] for it in items if it["kind"] == "cue_new"]
    for f in album["files"]:
        if f["file_id"] not in out and generated:
            out[f["file_id"]] = generated
    return out


def _has_link(root: str, path: str) -> bool:
    """True when `path` is outside `root` or any component of it below `root` is a symlink."""
    rel = os.path.relpath(path, root)
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        return True
    cur = root
    for part in rel.split(os.sep):
        if part in ("", "."):
            continue
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return False


def _check_batch_paths(root: str, folder: str) -> None:
    """The batch folder is <root>/_ORIGINALS/<batch>, with no link on the way and inside the collection
    folder: a folder that was moved away and replaced by a link must never be followed."""
    if os.path.dirname(os.path.normpath(folder)) != os.path.normpath(os.path.join(root, ORIGINALS)):
        raise RetireError("the originals folder of this batch is not where it should be")
    if _has_link(root, folder) or not _inside(folder, root):
        raise RetireError(f"the originals folder (or {ORIGINALS}) is a link now; refusing to move files through it")


_DB_STATE = {"intent": "intent", "claimed": "intent", "moved": "moved", "skipped": "skipped", "conflict": "conflict",
             "missing": "missing", "restoring": "moved", "restore_claimed": "moved", "restored": "restored"}


def _upsert(con, batch, *, file_id, unit, album, relpath, size, mtime_ns, content_id, kind, state) -> None:
    con.execute("INSERT OR REPLACE INTO retired_file(batch_id, file_id, unit_id, album, relpath, size, mtime_ns, "
                "content_id, kind, state) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (batch["id"], file_id, unit, album, relpath, size, mtime_ns, content_id, kind, state))


def _reconcile(con: sqlite3.Connection, batch: sqlite3.Row, j: protocol.Journal) -> None:
    """The journal is fsynced before every move, the database is not: after a crash the database may
    lack rows for files that were moved. Rebuild them from the journal, which is the truth."""
    for op, o in j.ops().items():
        if not op.startswith("f") or "relpath" not in o:
            continue
        _upsert(con, batch, file_id=o.get("file_id"), unit=o.get("unit"), album=o.get("album"),
                relpath=o["relpath"], size=o.get("size"), mtime_ns=o.get("mtime_ns"),
                content_id=o.get("content_id"), kind=o.get("kind"), state=_DB_STATE.get(o["history"][-1], "intent"))
    con.commit()


def _is_parked(orig: str, parked: str, last_state: str | None, size: int, mtime_ns: int) -> bool:
    """Is this file already in the originals folder? The journal says so after the move; after a crash
    between the rename and its record, or between a link and the unlink, the disk is the truth."""
    if last_state == "moved":
        return True
    p_ours, o_ours = _is_ours(parked, size, mtime_ns), _is_ours(orig, size, mtime_ns)
    return bool(p_ours and (not o_ours or _same_inode(orig, parked)))


def _row_of(o: dict) -> dict:
    return dict(file_id=o.get("file_id"), unit=o.get("unit"), album=o.get("album"), relpath=o["relpath"],
                size=o.get("size"), mtime_ns=o.get("mtime_ns"), content_id=o.get("content_id"), kind=o.get("kind"))


def _settle_inflight(con: sqlite3.Connection, batch: sqlite3.Row, j: protocol.Journal) -> None:
    """A crash between the rename and its journal record leaves a file parked while the journal still says
    'intent' (or 'claimed'). The disk is the truth: record those moves, so that Restore sees them and no
    check that later halts an album can strand them. Also finishes a link-method move (one inode, two
    names). Must run before anything filters operations by their journal state."""
    for op, o in j.ops().items():
        if not op.startswith("f") or "src" not in o or o["history"][-1] not in ("intent", "claimed"):
            continue
        orig, parked, size, mtime = o["src"], o["dst"], o["size"], o["mtime_ns"]
        p_ours, o_ours = _is_ours(parked, size, mtime), _is_ours(orig, size, mtime)
        if p_ours and o_ours and _same_inode(orig, parked):        # a link-method move that lost power before the unlink
            os.unlink(orig)
            o_ours = False
        if p_ours and not o_ours:                                  # our file is parked; whatever is at the old name is not it
            _record(con, batch, j, op, _row_of(o), "moved", ino=os.lstat(parked).st_ino)


def _move_album(con, run: Run, batch: sqlite3.Row, j: protocol.Journal, album: dict) -> int:
    """Move the files of one album that was just checked. The journal gets the album's whole file list
    first, so a crash half-way through is finished by Resume with exactly these files."""
    folder = batch["folder"]
    uid = album["unit_id"]
    copies = _copies_of(run, album)
    with open(os.path.join(folder, MANIFEST), "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        for f in album["files"]:                              # the manifest first: it is the way back by hand
            w.writerow(_manifest_row(f, album, copies))
        fh.flush()
        os.fsync(fh.fileno())
    files = [{k: f[k] for k in ("file_id", "path", "relpath", "size", "mtime_ns", "content_id", "kind")}
             for f in album["files"]]
    j.write({"op": f"a{uid}", "state": "album_started", "label": album["label"], "files": files})
    return _finish_album(con, batch, j, run.root, uid, album["label"], files)


def _finish_album(con, batch: sqlite3.Row, j: protocol.Journal, root: str, uid: int, label: str,
                  files: list[dict]) -> int:
    """Move every file of the album that has not been moved yet, settling what a crash left."""
    folder, n = batch["folder"], 0
    _check_batch_paths(root, folder)
    ops = j.ops()
    for f in files:
        op = f"f{f['file_id']}"
        o = ops.get(op, {})
        hist = o.get("history", [])
        if hist and hist[-1] == "moved":
            n += 1
            continue
        src, dst = f["path"], os.path.join(folder, f["relpath"])
        row = dict(file_id=f["file_id"], unit=uid, album=label, relpath=f["relpath"], size=f["size"],
                   mtime_ns=f["mtime_ns"], content_id=f["content_id"], kind=f["kind"])
        if not hist:
            j.write({"op": op, "state": "intent", "src": src, "dst": dst, **{k: v for k, v in row.items()
                                                                           if k != "file_id"}, "file_id": f["file_id"]})
            _upsert(con, batch, state="intent", **row)
        s_there, d_there = os.path.lexists(src), os.path.lexists(dst)
        if s_there and d_there:
            if _same_inode(src, dst):                             # a link-method move that crashed before the unlink
                os.unlink(src)
                s_there = False
            elif hist and hist[-1] == "claimed" and _is_placeholder(dst, o.get("placeholder_ino")):
                os.unlink(dst)                                         # our own empty placeholder from a crashed move
                d_there = False
        if d_there and not s_there:                                    # the rename happened before a crash
            _record(con, batch, j, op, row, "moved", ino=os.stat(dst).st_ino)
            n += 1
        elif s_there and not d_there:
            try:
                _stat_same(src, f["size"], f["mtime_ns"])              # still the file that was checked
                _no_links(root, f["relpath"])
            except _Skip as e:
                _record(con, batch, j, op, row, "skipped", reason=str(e))
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if _has_link(root, os.path.dirname(dst)) or not _inside(dst, folder):
                raise RetireError("a target path leaves the batch folder")
            try:
                _rename_noreplace(src, dst, claimed=lambda ino, op=op: j.write(
                    {"op": op, "state": "claimed", "placeholder_ino": ino}))
            except FileExistsError:
                _record(con, batch, j, op, row, "conflict", reason="something is already at the target; nothing replaced")
                continue
            _record(con, batch, j, op, row, "moved", ino=os.stat(dst).st_ino)
            n += 1
        elif s_there:
            _record(con, batch, j, op, row, "conflict", reason="both paths exist")
        else:
            _record(con, batch, j, op, row, "missing", reason="neither path exists")
    j.write({"op": f"a{uid}", "state": "album_done"})
    con.commit()
    return n


def _record(con, batch, j, op: str, row: dict, state: str, **extra) -> None:
    j.write({"op": op, "state": state, **extra})
    _upsert(con, batch, state=state, **row)
    con.commit()


def retire(con: sqlite3.Connection, run_id: int, progress=None, stop=None) -> dict:
    """Check each album of the run again and move the ones that verify. One batch per call; its folder
    is created when the first album is ready to move (nothing is written when nothing qualifies)."""
    run = Run(con, run_id)
    require_allowed(con, run.root_id)
    return _run_batch(con, run, None, progress, stop)


_HANDLED = ("album_done", "album_skipped", "album_halted")


def _run_batch(con, run: Run, batch: sqlite3.Row | None, progress, stop, carried: list[dict] | None = None) -> dict:
    j = protocol.Journal(Path(batch["journal"])) if batch is not None else None
    if batch is not None:
        _check_batch_paths(run.root, batch["folder"])
    done_units = {int(k[1:]) for k, o in (j.ops().items() if j else []) if k.startswith("a") and
                  o["history"][-1] in _HANDLED}
    todo = [u for u in sorted(k for k in run.units if k is not None) if u not in done_units]
    skipped, stopped = list(carried or []), False
    for i, uid in enumerate(todo, 1):
        if stop is not None and stop.is_set():
            stopped = True
            break
        album = check_album(con, run, uid)
        if album["eligible"]:
            if batch is None:
                batch = _new_batch(con, run)
                j = protocol.Journal(Path(batch["journal"]))
            _move_album(con, run, batch, j, album)
        else:
            skipped.append({"unit_id": uid, "label": album["label"], "reason": album["reason"]})
            if j is not None:
                j.write({"op": f"a{uid}", "state": "album_skipped", "reason": album["reason"]})
        if progress:
            progress(i, len(todo))
    if batch is None:
        return {"batch": None, "status": "stopped" if stopped else "complete", "albums": 0, "files": 0,
                "bytes": 0, "skipped": skipped}
    prev = json.loads(batch["summary_json"] or "{}")
    # totals from what was recorded as moved, so a resumed batch reports the whole batch, not its last pass
    files, size, albums = con.execute("SELECT count(*), coalesce(sum(size), 0), count(DISTINCT unit_id) "
                                      "FROM retired_file WHERE batch_id=? AND state IN ('moved', 'restored')",
                                      (batch["id"],)).fetchone()
    summary = {"albums": albums, "files": files, "bytes": size, "skipped": prev.get("skipped", []) + skipped}
    status = "stopped" if stopped else "complete"
    con.execute("UPDATE retire_batch SET status=?, finished_at=?, summary_json=? WHERE id=?",
                (status, time.time(), json.dumps(summary), batch["id"]))
    con.commit()
    return {"batch": batch["id"], "key": batch["key"], "folder": batch["folder"], "status": status, **summary}


def resume(con: sqlite3.Connection, batch_id: int, progress=None, stop=None) -> dict:
    """Finish an interrupted or stopped batch. The album that was in flight is checked AGAIN first,
    like any album (the copies, the originals still in place, links), taking its already parked files
    into account; if it no longer verifies, its remaining files stay where they are and the album is
    reported. Then the albums not handled yet are checked and moved as usual."""
    batch = con.execute("SELECT * FROM retire_batch WHERE id=?", (batch_id,)).fetchone()
    if batch is None:
        raise RetireError("no such batch")
    run = Run(con, batch["run_id"])
    require_allowed(con, run.root_id)
    _check_batch_paths(run.root, batch["folder"])
    j = protocol.Journal(Path(batch["journal"]))
    _settle_inflight(con, batch, j)
    _reconcile(con, batch, j)
    ops, halted = j.ops(), []
    for op, o in ops.items():
        if not (op.startswith("a") and o["history"][-1] == "album_started"):
            continue
        uid, files = int(op[1:]), o["files"]
        moved = {f["file_id"]: os.path.join(batch["folder"], f["relpath"]) for f in files
                 if _is_parked(f["path"], os.path.join(batch["folder"], f["relpath"]),
                               ops.get(f"f{f['file_id']}", {}).get("history", [None])[-1], f["size"], f["mtime_ns"])}
        album = check_album(con, run, uid, moved) if uid in run.units else \
            {"eligible": False, "reason": "no longer part of the run"}
        if album["eligible"]:
            _finish_album(con, batch, j, run.root, uid, o.get("label", ""), files)
        else:
            j.write({"op": op, "state": "album_halted", "reason": album["reason"]})
            halted.append({"unit_id": uid, "label": o.get("label", ""),
                           "reason": f"stopped half-way; the files not yet moved stay where they are ({album['reason']})"})
    return _run_batch(con, run, batch, progress, stop, halted)


# ---- restore ---------------------------------------------------------------------------------------
def restore(con: sqlite3.Connection, batch_id: int, unit_ids: set[int] | None = None) -> dict:
    """Move retired files back to their original paths. Never overwrites (the same no-replace ladder as
    everywhere): a file that has appeared at the original path since is reported and left, and so is
    the retired one. Refuses to work through a link: a batch folder that was moved away and replaced by
    a link is not followed."""
    batch = con.execute("SELECT * FROM retire_batch WHERE id=?", (batch_id,)).fetchone()
    if batch is None:
        raise RetireError("no such batch")
    root = con.execute("SELECT path FROM root WHERE id=?", (batch["root_id"],)).fetchone()["path"]
    _check_batch_paths(root, batch["folder"])
    j = protocol.Journal(Path(batch["journal"]))
    _settle_inflight(con, batch, j)
    _reconcile(con, batch, j)
    restored, blocked, missing = 0, [], []
    for op, o in j.ops().items():
        last = o["history"][-1]
        if not op.startswith("f") or last not in ("moved", "restoring", "restore_claimed"):
            continue
        if unit_ids is not None and o.get("unit") not in unit_ids:
            continue
        orig, parked = o["src"], o["dst"]
        row = _row_of(o)
        if _has_link(root, parked) or not _inside(parked, batch["folder"]):
            raise RetireError("a path in the journal leads outside the originals folder (or through a link)")
        if _has_link(root, os.path.dirname(orig)) or os.path.islink(orig):
            blocked.append({"relpath": o["relpath"], "reason": "a link or an outside folder on the path of the original place"})
            continue
        o_there, p_there = os.path.lexists(orig), os.path.lexists(parked)
        if o_there and p_there:
            if _same_inode(orig, parked):                         # a link-method restore that crashed before the unlink
                os.unlink(parked)
                p_there = False
            elif last == "restore_claimed" and _is_placeholder(orig, o.get("placeholder_ino")):
                os.unlink(orig)                                        # our own empty placeholder from a crashed restore
                o_there = False
        if o_there and not p_there:                                    # restored already (journal not written)
            _record(con, batch, j, op, row, "restored")
            restored += 1
            continue
        if o_there:
            blocked.append({"relpath": o["relpath"], "reason": "something is at the original path now"})
            continue
        if not p_there:
            missing.append({"relpath": o["relpath"], "reason": "no longer in the originals folder"})
            continue
        os.makedirs(os.path.dirname(orig), exist_ok=True)
        j.write({"op": op, "state": "restoring"})
        try:
            _rename_noreplace(parked, orig, claimed=lambda ino, op=op: j.write(
                {"op": op, "state": "restore_claimed", "placeholder_ino": ino}))
        except FileExistsError:
            j.write({"op": op, "state": "moved"})
            blocked.append({"relpath": o["relpath"], "reason": "something is at the original path now"})
            continue
        _record(con, batch, j, op, row, "restored")
        restored += 1
    _tidy_dirs(batch["folder"])
    left = con.execute("SELECT count(*) FROM retired_file WHERE batch_id=? AND state='moved'", (batch_id,)).fetchone()[0]
    if left == 0:
        con.execute("UPDATE retire_batch SET restored_at=? WHERE id=?", (time.time(), batch_id))
    con.commit()
    return {"batch": batch_id, "restored": restored, "blocked": blocked, "missing": missing}


def _tidy_dirs(folder: str) -> None:
    """Remove the empty sub-folders of a batch (never files: the README and manifest stay, and never a link)."""
    for dirpath, _dirs, _files in os.walk(folder, topdown=False):
        if dirpath != folder and not os.path.islink(dirpath):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


# ---- for the UI ------------------------------------------------------------------------------------
def batches(con: sqlite3.Connection, run_ids: list[int]) -> list[dict]:
    if not run_ids:
        return []
    out = []
    for b in con.execute(f"SELECT * FROM retire_batch WHERE run_id IN ({','.join('?' * len(run_ids))}) "
                         "ORDER BY id DESC", run_ids):
        d = dict(b)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        d["moved"] = con.execute("SELECT count(*) FROM retired_file WHERE batch_id=? AND state='moved'",
                                 (b["id"],)).fetchone()[0]
        out.append(d)
    return out
