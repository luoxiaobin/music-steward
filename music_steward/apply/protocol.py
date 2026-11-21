"""Journal-first, crash-safe publish of one file (proven in M0 on local disk and the NAS).

  intent  (journalled BEFORE touching the destination: source stat + hashes, final, temp)
  -> copy into ".<stem>.mlt-<run>-<op>.tmp<ext>" in the destination folder (O_EXCL), hashing
  -> source unchanged? -> copied -> write tags to the temp -> audio hash unchanged? -> tagged
  -> publish without overwriting (renamex RENAME_EXCL | link+unlink | O_EXCL claim+rename)
  -> published -> committed (ownership 'created')
Recovery reads the journal AND the files, never memory. Undo removes only 'created'
files whose SHA-256 and inode are unchanged since publish.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from ..hashing import copy_audio_fingerprint, file_sha256
from .tagwrite import write_tags

CHUNK = 1 << 20
_libc = ctypes.CDLL(None, use_errno=True)
RENAME_EXCL = 0x00000004
TERMINAL = {"committed", "conflict", "already_existed", "aborted_source_changed",
            "aborted_audio_changed", "ownership_uncertain", "undone", "undo_refused_modified", "failed",
            "aborted_unsafe_destination", "aborted_split_mismatch"}


def rename_excl(src, dst):
    if not hasattr(_libc, "renamex_np"):
        raise OSError(errno.ENOTSUP, "renamex_np not available")
    if _libc.renamex_np(os.fsencode(src), os.fsencode(dst), RENAME_EXCL) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(dst))


def choose_method(dest_root: Path) -> str:
    """Probe the destination once: best no-overwrite publish primitive it supports."""
    d = dest_root / f".mlt-probe-{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        a, b, c = d / "a", d / "b", d / "c"
        a.write_bytes(b"a")
        c.write_bytes(b"c")
        try:
            rename_excl(a, b)
            try:
                rename_excl(b, c)
            except FileExistsError:
                return "renamex_excl"
            except OSError:
                pass
            b.rename(a) if b.exists() else None
        except OSError:
            pass
        try:
            os.link(a, b)
            try:
                os.link(a, c)
            except FileExistsError:
                return "link_unlink"
        except OSError:
            pass
        try:
            os.close(os.open(c, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            return "claim_rename"
        return "none"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def fsync_dir(d: Path):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class Journal:
    """Append-only JSONL on LOCAL disk, fsync'd per record (a dropped share can't eat it)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: dict):
        with open(self.path, "a+b") as fh:
            # a crash can leave a torn last line without its newline; start on a fresh line so the
            # torn one is the only record lost (it is skipped on read) and this one is kept
            if fh.tell() > 0:
                fh.seek(-1, os.SEEK_END)
                torn = fh.read(1) != b"\n"
            else:
                torn = False
            fh.write((("\n" if torn else "") + json.dumps({"ts": time.time(), **rec}, ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def ops(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        if not self.path.exists():
            return merged
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:  # torn last line after a crash
                continue
            if str(r.get("op", "")).startswith("_"):     # run-level records (see meta())
                continue
            m = merged.setdefault(r["op"], {"history": []})
            m["history"].append(r["state"])
            m.update({k: v for k, v in r.items() if k != "ts"})
        return merged


    def meta(self) -> dict:
        """Run-level records (op "_run"), merged: {"planned": n} once every op of the plan is
        journalled. A run without it was interrupted while being planned: Resume completes it."""
        out: dict = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("op") == "_run":
                    out.update({k: v for k, v in r.items() if k not in ("ts", "op", "state")})
        return out


def temp_path(final: Path, run: str, op: str) -> Path:
    return final.with_name(f".{final.stem}.mlt-{run}-{op}.tmp{final.suffix}")


def plan_op(j: Journal, run: str, op: str, src: Path, final: Path, kind: str, codec: str | None,
            tags: dict, method: str, src_size: int, src_mtime_ns: int):
    rec = {"op": op, "state": "intent", "run": run, "kind": kind, "codec": codec, "src": str(src),
           "final": str(final), "temp": str(temp_path(final, run, op)), "tags": tags, "method": method,
           "src_size": src_size, "src_mtime_ns": src_mtime_ns}
    if final.exists():
        rec["final_preexisting_sha"] = file_sha256(str(final))
    j.write(rec)


def _crash(point, crash_at):
    if point == crash_at:
        os._exit(137)


def _cut(j: Journal, o: dict, src: Path, temp: Path, sp: dict, crash_at: str | None) -> bool:
    """A track from a cue-sheet image: cut it (exclusive temp), check the source did not change and
    that the track's samples equal the image's, journal 'copied'. False: aborted (journalled)."""
    from .split import extract, verify
    op = o["op"]
    os.close(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    extract(str(src), str(temp), sp["start"], sp["end"], sp["rate"], sp.get("bits"), sp["src_ext"])
    _crash("mid_copy", crash_at)
    st2 = src.stat()
    if st2.st_size != o["src_size"] or st2.st_mtime_ns != o["src_mtime_ns"]:
        temp.unlink(missing_ok=True)
        j.write({"op": op, "state": "aborted_source_changed", "ownership": "none",
                 "reason": "source changed during the copy"})
        return False
    if not verify(str(src), str(temp), sp["start"], sp["end"], sp["rate"], sp.get("bits"), sp["src_ext"]):
        temp.unlink(missing_ok=True)
        j.write({"op": op, "state": "aborted_split_mismatch", "ownership": "none",
                 "reason": "the track's audio differs from the same part of the image"})
        return False
    src_audio = copy_audio_fingerprint(str(temp), ".flac", "flac")
    j.write({"op": op, "state": "copied", "src_sha": file_sha256(str(temp)), "src_audio": src_audio})
    o["src_audio"] = src_audio
    _crash("after_copy", crash_at)
    return True


def execute_op(j: Journal, o: dict, crash_at: str | None = None, guard=None):
    """guard(final) -> None, or the reason this path must not be written (see runner._guard)."""
    src, final, temp = Path(o["src"]), Path(o["final"]), Path(o["temp"])
    op, state = o["op"], o["history"][-1]
    if state == "intent" and guard is not None and (why := guard(final)):
        j.write({"op": op, "state": "aborted_unsafe_destination", "ownership": "none", "reason": why})
        return
    sp = (o.get("tags") or {}).get("split")      # a track cut from a cue-sheet image (apply/split.py)
    if state == "intent":
        generated = (o.get("tags") or {}).get("generated_text")   # a cue sheet written by the plan
        if "final_preexisting_sha" in o or final.exists():
            if sp is not None:          # a track: the same samples count as the same file (tags may differ)
                from .split import SplitError, verify
                try:
                    same = final.exists() and verify(str(src), str(final), sp["start"], sp["end"], sp["rate"],
                                                     sp.get("bits"), sp["src_ext"])
                except SplitError:
                    same = False
            else:
                want = hashlib.sha256(generated.encode("utf-8-sig")).hexdigest() if generated is not None \
                    else file_sha256(str(src))
                same = final.exists() and file_sha256(str(final)) == want
            j.write({"op": op, "state": "already_existed" if same else "conflict",
                     "ownership": "already_existed" if same else "not_ours",
                     "reason": "identical file already at destination" if same else "a different file is already there"})
            return
        st = src.stat()
        if st.st_size != o["src_size"] or st.st_mtime_ns != o["src_mtime_ns"]:
            j.write({"op": op, "state": "aborted_source_changed", "ownership": "none",
                     "reason": "source changed since the plan was made"})
            return
        final.parent.mkdir(parents=True, exist_ok=True)
        if guard is not None and (why := guard(final)):      # the folders just made resolve somewhere else
            j.write({"op": op, "state": "aborted_unsafe_destination", "ownership": "none", "reason": why})
            return
        if sp is not None:
            if not _cut(j, o, src, temp, sp, crash_at):
                return
        else:
            h = hashlib.sha256()
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                if generated is not None:      # no source to copy: write the plan's text (src = a guard)
                    data = generated.encode("utf-8-sig")
                    os.write(fd, data)
                    h.update(data)
                else:
                    with open(src, "rb") as fin:
                        for i, buf in enumerate(iter(lambda: fin.read(CHUNK), b"")):
                            os.write(fd, buf)
                            h.update(buf)
                            if i == 0:
                                _crash("mid_copy", crash_at)  # copy started, not yet complete or journalled
                os.fsync(fd)
            finally:
                os.close(fd)
            st2 = src.stat()
            if st2.st_size != o["src_size"] or st2.st_mtime_ns != o["src_mtime_ns"]:
                temp.unlink(missing_ok=True)
                j.write({"op": op, "state": "aborted_source_changed", "ownership": "none",
                         "reason": "source changed during the copy"})
                return
            # the copy must be byte-identical to what was read from the source (local read, fast);
            # the audio fingerprint is then taken from the LOCAL copy, not by re-reading the source
            if file_sha256(str(temp)) != h.hexdigest():
                temp.unlink(missing_ok=True)
                j.write({"op": op, "state": "aborted_copy_mismatch", "ownership": "none",
                         "reason": "the copy differs from the bytes read from the source"})
                return
            src_audio = copy_audio_fingerprint(str(temp), src.suffix, o["codec"]) if o["kind"] == "audio" else None
            j.write({"op": op, "state": "copied", "src_sha": h.hexdigest(), "src_audio": src_audio})
            o["src_audio"] = src_audio
            _crash("after_copy", crash_at)
        state = "copied"
    if state == "copied":
        if o["kind"] == "audio" and o.get("tags"):
            write_tags(str(temp), o["tags"], o["codec"])
        if o["kind"] == "split":
            write_tags(str(temp), {k: v for k, v in o["tags"].items() if k != "split"}, "flac")
        if o["kind"] == "cue" and (o.get("tags") or {}).get("cue_rewrite"):
            from ..cue import rewrite_bytes
            rw = o["tags"]["cue_rewrite"]
            data, _enc = rewrite_bytes(temp.read_bytes(), rw.get("files"), rw.get("album"),
                                       {int(k): v for k, v in (rw.get("tracks") or {}).items()},
                                       out_encoding=rw.get("encoding"),   # plans before 2026-11-19: original
                                       keep_file=rw.get("keep_file"))
            with open(temp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        if o["kind"] in ("audio", "split"):
            after = copy_audio_fingerprint(str(temp), temp.suffix, o["codec"])
            if after != o.get("src_audio"):
                temp.unlink(missing_ok=True)
                j.write({"op": op, "state": "aborted_audio_changed", "ownership": "none",
                         "reason": "audio of the copy differs from the source after tagging"})
                return
        st = temp.stat()
        j.write({"op": op, "state": "tagged", "tagged_sha": file_sha256(str(temp)), "temp_ino": st.st_ino})
        o.update(tagged_sha=file_sha256(str(temp)), temp_ino=st.st_ino)
        _crash("after_tag", crash_at)
        state = "tagged"
    if state == "tagged":
        try:
            if o["method"] == "renamex_excl":
                rename_excl(temp, final)
            elif o["method"] == "link_unlink":
                os.link(temp, final)
                temp.unlink()
            elif o["method"] == "claim_rename":
                j.write({"op": op, "state": "claiming", "claim_ts": time.time()})
                os.close(os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
                pino = final.stat().st_ino
                j.write({"op": op, "state": "claimed", "placeholder_ino": pino})
                st = final.stat()
                if st.st_ino != pino or st.st_size != 0:
                    temp.unlink(missing_ok=True)
                    j.write({"op": op, "state": "conflict", "ownership": "not_ours",
                             "reason": "placeholder replaced by someone else"})
                    return
                os.rename(temp, final)
            else:
                raise OSError("no safe publish method on this destination")
        except FileExistsError:
            temp.unlink(missing_ok=True)
            j.write({"op": op, "state": "conflict", "ownership": "not_ours",
                     "reason": "a file appeared at the destination before publish"})
            return
        fsync_dir(final.parent)
        _crash("after_publish", crash_at)
        j.write({"op": op, "state": "published", "final_ino": final.stat().st_ino, "final_sha": o["tagged_sha"]})
        state = "published"
    if state == "published":
        j.write({"op": op, "state": "committed", "ownership": "created"})


def retry_failed(j: Journal) -> int:
    """Make the run's 'failed' files retryable (drive disconnected, disk full...): each goes back
    to the state it was in when it failed, and recover() then decides from the files, as after a
    crash -- so a copy that had in fact been published is recognised as ours, not re-copied.
    Conflicts and changed sources stay as they are: retrying cannot fix them."""
    n = 0
    for op, o in j.ops().items():
        h = o["history"]
        if h[-1] != "failed":
            continue
        prev = next((s for s in reversed(h[:-1]) if s not in TERMINAL), "intent")
        j.write({"op": op, "state": prev, "retry_of": "failed", "reason": None})
        n += 1
    return n


def recover(j: Journal) -> list[dict]:
    """Bring every unfinished op to a resumable or terminal state, from the files."""
    out = []
    for op, o in j.ops().items():
        state = o["history"][-1]
        if state in TERMINAL:
            continue
        final, temp = Path(o["final"]), Path(o["temp"])
        d = {"op": op, "last_state": state}
        if state in ("intent", "copied"):
            temp.unlink(missing_ok=True)
            j.write({"op": op, "state": "intent", "recovered_from": state})
            d["action"] = "restart"
        elif state in ("tagged", "claiming", "claimed"):
            if final.exists():
                st = final.stat()
                if st.st_size == 0 and (state == "claimed" and st.st_ino == o.get("placeholder_ino")
                                        or state == "claiming" and st.st_mtime >= o.get("claim_ts", 0) - 2):
                    final.unlink()  # our own empty placeholder
                    j.write({"op": op, "state": "tagged" if temp.exists() else "intent", "recovered_from": state})
                    d["action"] = "own placeholder removed"
                elif st.st_ino == o.get("temp_ino") and file_sha256(str(final)) == o.get("tagged_sha"):
                    temp.unlink(missing_ok=True)
                    j.write({"op": op, "state": "published", "final_ino": st.st_ino, "final_sha": o["tagged_sha"]})
                    d["action"] = "publish had completed"
                else:
                    temp.unlink(missing_ok=True)
                    j.write({"op": op, "state": "conflict", "ownership": "not_ours",
                             "reason": "someone else's file at the destination"})
                    d["action"] = "conflict"
            elif temp.exists() and file_sha256(str(temp)) == o.get("tagged_sha"):
                j.write({"op": op, "state": "tagged", "recovered_from": state})
                d["action"] = "resume publish"
            else:
                temp.unlink(missing_ok=True)
                j.write({"op": op, "state": "intent", "recovered_from": state})
                d["action"] = "restart"
        elif state == "published":
            temp.unlink(missing_ok=True)
            d["action"] = "commit"
        out.append(d)
    return out


def run_all(j: Journal, crash_at: str | None = None, crash_op: str | None = None, progress=None, stop=None,
            guard=None):
    """stop: a threading.Event -- checked between files; the rest stay 'intent' (Resume continues)."""
    ops = [(op, o) for op, o in j.ops().items() if o["history"][-1] not in TERMINAL]
    for i, (op, o) in enumerate(ops, 1):
        if stop is not None and stop.is_set():
            break
        try:
            execute_op(j, o, crash_at if op == crash_op else None, guard)
        except Exception as e:  # one bad file must not stop the run; the journal says what happened
            Path(o["temp"]).unlink(missing_ok=True)
            j.write({"op": op, "state": "failed", "ownership": "none", "reason": f"{type(e).__name__}: {e}"[:300]})
        if progress:
            progress(i, len(ops))


def undo(j: Journal) -> list[dict]:
    out = []
    for op, o in j.ops().items():
        final = Path(o["final"])
        rec = {"op": op, "final": str(final), "ownership": o.get("ownership")}
        if o.get("ownership") != "created" or o["history"][-1] in ("undone",):
            rec["action"] = "untouched (not created by this run)" if o.get("ownership") != "created" else "already undone"
        elif not final.exists():
            rec["action"] = "already gone"
        elif file_sha256(str(final)) != o["final_sha"] or final.stat().st_ino != o["final_ino"]:
            j.write({"op": op, "state": "undo_refused_modified"})
            rec["action"] = "kept: changed since it was created"
        else:
            final.unlink()
            j.write({"op": op, "state": "undone"})
            rec["action"] = "removed"
            try:  # tidy now-empty folders the run created
                final.parent.rmdir()
                final.parent.parent.rmdir()
            except OSError:
                pass
        out.append(rec)
    return out
