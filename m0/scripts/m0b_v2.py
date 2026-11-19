#!/usr/bin/env python3
"""
M0b v2 — journal-first apply protocol, crash injection, ownership, undo,
tag preservation, audio-only verification and side-file relinking.

  ./venv/bin/python -I scripts/m0b_v2.py --dest <DIR> [--label nas]

<DIR> is the ONLY place this script writes besides m0/scratch (local
journal + fixtures). It must be an empty-or-scratch folder: the script
refuses to run if it contains anything it did not create (marker file).
Sources are copies under m0/scratch; nas_sample/ is only read.

Results: results/m0b_<label>_result.json
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAS_SAMPLE = ROOT / "nas_sample"
SCRATCH = ROOT / "scratch" / "m0b"
RESULTS = ROOT / "results"
MARKER = ".mlt-m0b-scratch"
CHUNK = 256 * 1024

_libc = ctypes.CDLL(None, use_errno=True)
RENAME_EXCL = 0x00000004  # <sys/stdio.h> on macOS


# =========================================================== primitives
def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def audio_hash(p: Path) -> str:
    """sha256 of decoded PCM of the first audio stream: unaffected by tags."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:a:0",
                        "-f", "s16le", "-acodec", "pcm_s16le", "-"], capture_output=True)
    if r.returncode or not r.stdout:
        return "DECODE-FAILED:" + r.stderr.decode(errors="replace")[:120]
    return hashlib.sha256(r.stdout).hexdigest()


def rename_excl(src: Path, dst: Path):
    if _libc.renamex_np(os.fsencode(src), os.fsencode(dst), RENAME_EXCL) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(dst))


def fsync_dir(d: Path):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# =========================================================== 1. probe
def probe_publish(dest: Path) -> dict:
    d = dest / "_probe"
    d.mkdir()
    a, b, c = d / "a", d / "b", d / "c"
    a.write_bytes(b"A" * 1000)
    c.write_bytes(b"existing")
    out = {"fs": _fs_type(dest)}

    def attempt(name, fn, expect_exists=False):
        try:
            fn()
            out[name] = "ok" if not expect_exists else "OVERWROTE (unsafe)"
        except OSError as e:
            out[name] = ("refused EEXIST" if e.errno == errno.EEXIST else
                         f"error {errno.errorcode.get(e.errno, e.errno)}: {e.strerror}")

    attempt("hardlink_new", lambda: os.link(a, b))
    if b.exists():
        out["hardlink_shares_inode"] = os.stat(a).st_ino == os.stat(b).st_ino
        out["hardlink_nlink"] = os.stat(a).st_nlink
        b.unlink()
    attempt("hardlink_onto_existing", lambda: os.link(a, c), expect_exists=True)
    out["existing_untouched_after_link"] = c.read_bytes() == b"existing"

    a2 = d / "a2"
    a2.write_bytes(b"B" * 1000)
    ino_before = os.stat(a2).st_ino
    attempt("renamex_excl_new", lambda: rename_excl(a2, d / "r"))
    if (d / "r").exists():
        out["renamex_preserves_inode"] = os.stat(d / "r").st_ino == ino_before
    a3 = d / "a3"
    a3.write_bytes(b"C")
    attempt("renamex_excl_onto_existing", lambda: rename_excl(a3, c), expect_exists=True)
    out["existing_untouched_after_renamex"] = c.read_bytes() == b"existing"

    a4 = d / "a4"
    a4.write_bytes(b"D")
    os.rename(a4, d / "c_plain")  # control
    (d / "c2").write_bytes(b"victim")
    (d / "a5").write_bytes(b"E")
    os.rename(d / "a5", d / "c2")
    out["plain_rename_overwrites"] = (d / "c2").read_bytes() == b"E"

    def oexcl():
        fd = os.open(c, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    attempt("o_excl_onto_existing", oexcl, expect_exists=True)

    # inode stability across reopen (needed for ownership proofs)
    out["inode_stable_across_stat"] = os.stat(c).st_ino == os.stat(c).st_ino
    shutil.rmtree(d)

    if out.get("renamex_excl_new") == "ok" and out.get("renamex_excl_onto_existing") == "refused EEXIST":
        method = "renamex_excl"
    elif out.get("hardlink_new") == "ok" and out.get("hardlink_onto_existing") == "refused EEXIST":
        method = "link_unlink"
    else:
        method = "none"
    out["chosen_method"] = method
    return out


def _fs_type(p: Path) -> str:
    try:
        r = subprocess.run(["stat", "-f", "%T", str(p)], capture_output=True, text=True)
        df = subprocess.run(["df", str(p)], capture_output=True, text=True).stdout.splitlines()[-1].split()[0]
        return f"{r.stdout.strip()} ({df})"
    except Exception:
        return "?"


# =========================================================== 2. journal
class Journal:
    """Append-only JSONL, fsync'd per record, always on LOCAL disk."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: dict):
        rec = {"ts": time.time(), **rec}
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # torn last line after a crash
                pass
        return out

    def ops(self) -> dict[str, dict]:
        """op_id -> merged state (later records override earlier fields)."""
        merged: dict[str, dict] = {}
        for r in self.records():
            m = merged.setdefault(r["op"], {"history": []})
            m["history"].append(r["state"])
            m.update({k: v for k, v in r.items() if k not in ("ts",)})
        return merged


TERMINAL = {"committed", "conflict", "already_existed", "aborted_source_changed",
            "ownership_uncertain", "undone", "undo_refused_modified"}


def temp_name(final: Path, run: str, op: str) -> Path:
    return final.with_name(f".{final.stem}.mlt-{run}-{op}.tmp{final.suffix}")


class Crash(Exception):
    pass


def maybe_crash(point: str, crash_at: str | None):
    if point == crash_at:
        os._exit(137)  # like kill -9: no finally, no flush, no cleanup


def plan_op(j: Journal, run: str, op: str, src: Path, final: Path, edits: dict, method: str):
    st = src.stat()
    rec = {"op": op, "state": "intent", "run": run, "src": str(src), "final": str(final),
           "temp": str(temp_name(final, run, op)), "edits": edits, "method": method,
           "src_size": st.st_size, "src_mtime_ns": st.st_mtime_ns, "src_sha": sha_file(src),
           "src_audio": audio_hash(src)}
    if final.exists():
        rec["final_preexisting_sha"] = sha_file(final)
        rec["final_preexisting_audio"] = audio_hash(final)
    j.write(rec)
    return rec


def execute_op(j: Journal, o: dict, crash_at: str | None = None, hooks: dict | None = None):
    """Run one op from wherever its journal says it is. Never trusts memory."""
    hooks = hooks or {}
    src, final, temp = Path(o["src"]), Path(o["final"]), Path(o["temp"])
    state = o["history"][-1] if "history" in o else o["state"]
    op = o["op"]

    if state == "intent":
        maybe_crash("after_intent", crash_at)
        if "final_preexisting_sha" in o:
            same_audio = o["final_preexisting_audio"] == o["src_audio"]
            j.write({"op": op, "state": "already_existed" if same_audio else "conflict",
                     "reason": "identical audio already at destination" if same_audio
                     else "different file already at destination at plan time",
                     "ownership": "already_existed" if same_audio else "not_ours"})
            return
        # ---- copy into a temp name that cannot collide (O_EXCL) ----------
        h = hashlib.sha256()
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with open(src, "rb") as fin:
                n = 0
                while True:
                    buf = fin.read(CHUNK)
                    if not buf:
                        break
                    if "mid_copy" in hooks and n == 1:
                        hooks["mid_copy"]()
                    os.write(fd, buf)
                    h.update(buf)
                    n += 1
                    if n == 2:
                        maybe_crash("mid_copy", crash_at)
            os.fsync(fd)
        finally:
            os.close(fd)
        st = src.stat()
        if (h.hexdigest() != o["src_sha"] or st.st_size != o["src_size"]
                or st.st_mtime_ns != o["src_mtime_ns"]):
            temp.unlink(missing_ok=True)
            j.write({"op": op, "state": "aborted_source_changed",
                     "reason": "source content/stat differs from plan (changed during copy)",
                     "ownership": "none"})
            return
        j.write({"op": op, "state": "copied", "temp_sha": h.hexdigest()})
        maybe_crash("after_copy", crash_at)
        state = "copied"

    if state == "copied":
        if not temp.exists() or sha_file(temp) != o.get("temp_sha", sha_file(temp)):
            raise RuntimeError("recovery should have handled this")
        apply_edits(temp, o["edits"])
        os.sync() if False else None
        st = temp.stat()
        j.write({"op": op, "state": "tagged", "tagged_sha": sha_file(temp),
                 "tagged_audio": audio_hash(temp), "temp_ino": st.st_ino})
        o.update({"tagged_sha": sha_file(temp), "temp_ino": st.st_ino})
        maybe_crash("after_tag", crash_at)
        state = "tagged"

    if state == "tagged":
        if "before_publish" in hooks:
            hooks["before_publish"]()
        try:
            if o["method"] == "renamex_excl":
                rename_excl(temp, final)
            elif o["method"] == "link_unlink":
                os.link(temp, final)
                maybe_crash("after_link_before_unlink", crash_at)
                temp.unlink()
            else:
                raise RuntimeError("no safe publish method on this destination")
        except FileExistsError:
            temp.unlink(missing_ok=True)
            j.write({"op": op, "state": "conflict", "ownership": "not_ours",
                     "reason": "a file appeared at the final name before publish"})
            return
        fsync_dir(final.parent)
        maybe_crash("after_publish_syscall", crash_at)
        st = final.stat()
        j.write({"op": op, "state": "published", "final_ino": st.st_ino,
                 "final_sha": o["tagged_sha"]})
        maybe_crash("after_published_journal", crash_at)
        state = "published"

    if state == "published":
        j.write({"op": op, "state": "committed", "ownership": "created"})


def recover(j: Journal) -> list[dict]:
    """Inspect journal + files; bring every op to a state execute_op can resume
    from, or to a terminal state. Returns the decisions taken."""
    decisions = []
    for op, o in j.ops().items():
        state = o["history"][-1]
        if state in TERMINAL:
            continue
        final, temp = Path(o["final"]), Path(o["temp"])
        fe, te = final.exists(), temp.exists()
        d = {"op": op, "last_state": state, "final_exists": fe, "temp_exists": te}
        if state in ("intent", "copied"):
            # never published from these states; a partial temp is garbage
            if te:
                temp.unlink()
            if state == "copied" and fe and "final_preexisting_sha" not in o:
                j.write({"op": op, "state": "intent_reset"})
            j.write({"op": op, "state": "intent", "recovered_from": state})
            d["action"] = "discarded temp, restart op from intent"
            if fe and "final_preexisting_sha" not in o:
                d["note"] = "final appeared while we were down -> will be a conflict on resume"
        elif state == "tagged":
            if fe:
                fst = final.stat()
                f_sha = sha_file(final)
                if f_sha == o["tagged_sha"] and fst.st_ino == o["temp_ino"]:
                    if te:
                        temp.unlink()  # link_unlink crash between link and unlink
                    j.write({"op": op, "state": "published", "final_ino": fst.st_ino,
                             "final_sha": f_sha, "recovered_from": "tagged"})
                    d["action"] = "final is ours (same inode + sha) -> mark published"
                elif f_sha == o["tagged_sha"]:
                    if te:
                        temp.unlink()
                    j.write({"op": op, "state": "ownership_uncertain", "ownership": "uncertain",
                             "reason": "final has our exact bytes but a different inode"})
                    d["action"] = "ownership uncertain -> left alone, needs review"
                else:
                    if te:
                        temp.unlink()
                    j.write({"op": op, "state": "conflict", "ownership": "not_ours",
                             "reason": "different file at final name after crash"})
                    d["action"] = "conflict (someone else's file), temp removed"
            elif te and sha_file(temp) == o["tagged_sha"]:
                d["action"] = "temp intact -> resume at publish"
            else:
                if te:
                    temp.unlink()
                j.write({"op": op, "state": "intent", "recovered_from": "tagged-temp-missing"})
                d["action"] = "temp missing/damaged -> restart op"
        elif state == "published":
            d["action"] = "published -> commit"
            if te:
                temp.unlink()
        decisions.append(d)
    return decisions


def run_all(j: Journal, crash_at: str | None = None, crash_op: str | None = None, hooks=None):
    for op, o in j.ops().items():
        if o["history"][-1] in TERMINAL:
            continue
        execute_op(j, o, crash_at if op == crash_op else None, (hooks or {}).get(op))


def undo(j: Journal) -> list[dict]:
    out = []
    for op, o in j.ops().items():
        own = o.get("ownership")
        final = Path(o["final"])
        rec = {"op": op, "ownership": own, "final_exists": final.exists()}
        if own != "created":
            rec["action"] = "not ours -> untouched"
            if "final_preexisting_sha" in o and final.exists():
                rec["preexisting_untouched"] = sha_file(final) == o["final_preexisting_sha"]
        elif not final.exists():
            rec["action"] = "already gone"
        elif sha_file(final) != o["final_sha"] or final.stat().st_ino != o["final_ino"]:
            j.write({"op": op, "state": "undo_refused_modified",
                     "reason": "output changed since publish"})
            rec["action"] = "REFUSED: modified since publish, left alone"
        else:
            final.unlink()
            j.write({"op": op, "state": "undone"})
            rec["action"] = "removed"
        out.append(rec)
    return out


# =========================================================== edits & tags
def apply_edits(path: Path, edits: dict):
    import mutagen
    if not edits:
        return
    f = mutagen.File(path)
    ext = path.suffix.lower()
    for k, v in edits.items():
        assert k == "title"
        if ext == ".mp3":
            from mutagen.id3 import TIT2
            f.tags.setall("TIT2", [TIT2(encoding=3, text=[v])])
        elif ext == ".flac":
            f["TITLE"] = [v]
        elif ext == ".ape":
            f["Title"] = v
    f.save()


def dump_tags(p: Path) -> dict:
    """Every field, with binary payloads reduced to sha256."""
    import mutagen
    f = mutagen.File(p)
    ext = p.suffix.lower()
    out = {}
    if f is None or f.tags is None:
        return out

    def b(x):
        return "sha:" + hashlib.sha256(x).hexdigest()[:16] if isinstance(x, (bytes, bytearray)) else x
    if ext == ".mp3":
        out["_id3_version"] = ".".join(map(str, f.tags.version))
        for key, fr in f.tags.items():
            v = {}
            for attr in ("text", "desc", "lang", "mime", "type", "owner", "url", "email", "rating", "count"):
                if hasattr(fr, attr):
                    val = getattr(fr, attr)
                    v[attr] = [str(t) for t in val] if isinstance(val, list) else (int(val) if attr == "type" else val)
            if hasattr(fr, "data"):
                v["data"] = b(fr.data)
            out[key] = v
    elif ext == ".flac":
        vc = {}
        for k, v in f.tags:
            vc.setdefault(k.upper(), []).append(v)
        out.update({f"VC:{k}": v for k, v in sorted(vc.items())})
        out["_vendor"] = f.tags.vendor
        out["PICTURES"] = [{"type": int(pic.type), "mime": pic.mime, "desc": pic.desc,
                            "w": pic.width, "h": pic.height, "data": b(pic.data)} for pic in f.pictures]
    elif ext == ".ape":
        # APEv2 item keys are case-insensitive by spec: compare on lower-case
        # keys and report the stored spelling separately.
        from mutagen.apev2 import APEBinaryValue
        out["_ape_key_spelling"] = sorted(f.tags.keys())
        for k, v in f.tags.items():
            if isinstance(v, APEBinaryValue):
                out[f"APE:{k.lower()}"] = {"kind": "binary", "data": b(bytes(v))}
            else:
                out[f"APE:{k.lower()}"] = {"kind": type(v).__name__, "values": list(v)}
    return out


def diff_tags(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {
        "dropped": [k for k in keys if k in a and k not in b],
        "added": [k for k in keys if k not in a and k in b],
        "changed": [k for k in keys if k in a and k in b and a[k] != b[k]],
        "preserved": [k for k in keys if k in a and k in b and a[k] == b[k]],
    }


def make_tagged_fixtures(fx: Path) -> dict[str, Path]:
    """MP3 + FLAC (encoded from a tone) and APE (copy of the real NAS APE),
    each tagged with custom tags, multiple artists, artwork, lyrics, RG."""
    fx.mkdir(parents=True, exist_ok=True)
    art = fx / "cover.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=300x300:rate=1",
                    "-frames:v", "1", str(art)], check=True)
    art_bytes = art.read_bytes()
    lyrics = "[00:00.00]第一行歌词 line one\n[00:05.00]第二行 line two"
    out = {}
    for ext, codec in ((".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]), (".flac", ["-c:a", "flac"])):
        p = fx / f"tagged{ext}"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=523:duration=12", *codec, "-map_metadata", "-1", str(p)], check=True)
        out[ext] = p
    # --- MP3: ID3v2.4
    from mutagen.id3 import (APIC, COMM, ID3, TALB, TCON, TIT2, TPE1, TPE2, TRCK, TXXX, USLT, TDRC)
    t = ID3()
    t.add(TIT2(encoding=3, text=["原始标题 Original Title"]))
    t.add(TPE1(encoding=3, text=["郑绪岚", "Guest Artist"]))  # multiple artists
    t.add(TPE2(encoding=3, text=["郑绪岚"]))
    t.add(TALB(encoding=3, text=["情系红楼梦"]))
    t.add(TRCK(encoding=3, text=["5/11"]))
    t.add(TDRC(encoding=3, text=["1987"]))
    t.add(TCON(encoding=3, text=["Soundtrack"]))
    t.add(COMM(encoding=3, lang="chi", desc="", text=["注释 comment"]))
    t.add(TXXX(encoding=3, desc="MLT_CUSTOM", text=["custom value 自定义"]))
    t.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["00000000-0000-0000-0000-000000000001"]))
    t.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-6.54 dB"]))
    t.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text=["0.987654"]))
    t.add(TXXX(encoding=3, desc="REPLAYGAIN_ALBUM_GAIN", text=["-7.10 dB"]))
    t.add(USLT(encoding=3, lang="chi", desc="", text=lyrics))
    t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front", data=art_bytes))
    t.save(out[".mp3"], v2_version=4)
    # --- FLAC: Vorbis comments + PICTURE block
    from mutagen.flac import FLAC, Picture
    f = FLAC(out[".flac"])
    f["TITLE"] = ["原始标题 Original Title"]
    f["ARTIST"] = ["郑绪岚", "Guest Artist"]
    f["ALBUMARTIST"] = ["郑绪岚"]
    f["ALBUM"] = ["情系红楼梦"]
    f["TRACKNUMBER"] = ["5"]
    f["DATE"] = ["1987"]
    f["MLT_CUSTOM"] = ["custom value 自定义"]
    f["MUSICBRAINZ_ALBUMID"] = ["00000000-0000-0000-0000-000000000001"]
    f["REPLAYGAIN_TRACK_GAIN"] = ["-6.54 dB"]
    f["REPLAYGAIN_TRACK_PEAK"] = ["0.987654"]
    f["REPLAYGAIN_ALBUM_GAIN"] = ["-7.10 dB"]
    f["LYRICS"] = [lyrics]
    pic = Picture()
    pic.type, pic.mime, pic.desc, pic.width, pic.height, pic.depth, pic.data = 3, "image/jpeg", "Front", 300, 300, 24, art_bytes
    f.add_picture(pic)
    f.save()
    # --- APE: real file from NAS sample (copied), APEv2 tags added
    real_ape = next(NAS_SAMPLE.rglob("*.ape"), None)
    if real_ape:
        p = fx / "tagged.ape"
        subprocess.run(["cp", "-c", str(real_ape), str(p)], check=True)
        from mutagen.apev2 import APEv2, APEValue, BINARY
        a = APEv2()
        a["Title"] = "原始标题 Original Title"
        a["Artist"] = ["柏菲", "Guest Artist"]  # multi-value
        a["Album Artist"] = "柏菲"
        a["Album"] = "HIFI年代 - 怀念战友"
        a["Track"] = "1"
        a["Year"] = "2004"
        a["MLT_CUSTOM"] = "custom value 自定义"
        a["MUSICBRAINZ_ALBUMID"] = "00000000-0000-0000-0000-000000000001"
        a["REPLAYGAIN_TRACK_GAIN"] = "-6.54 dB"
        a["REPLAYGAIN_TRACK_PEAK"] = "0.987654"
        a["Lyrics"] = lyrics
        a["Cover Art (Front)"] = APEValue(b"cover.jpg\x00" + art_bytes, BINARY)
        a.save(p)
        out[".ape"] = p
    return out


def tag_preservation(fx: dict[str, Path], work: Path) -> dict:
    from mediafile import MediaFile
    res = {}
    for ext, src in fx.items():
        r = {}
        for writer in ("mutagen_minimal", "beets_mediafile"):
            p = work / f"{writer}{ext}"
            subprocess.run(["cp", "-c", str(src), str(p)], check=True)
            before, audio_before = dump_tags(p), audio_hash(p)
            if writer == "mutagen_minimal":
                apply_edits(p, {"title": "Edited Title 已编辑"})
            else:
                mf = MediaFile(str(p))
                mf.title = "Edited Title 已编辑"
                mf.save()
            after, audio_after = dump_tags(p), audio_hash(p)
            d = diff_tags(before, after)
            unexpected = [k for k in d["changed"] if not re.search(r"TIT2|TITLE|_ape_key_spelling", k, re.I)]
            notes = []
            if before.get("_ape_key_spelling") != after.get("_ape_key_spelling"):
                notes.append(f"APE key spelling changed: {sorted(set(before['_ape_key_spelling']) - set(after['_ape_key_spelling']))}"
                             f" -> {sorted(set(after['_ape_key_spelling']) - set(before['_ape_key_spelling']))}"
                             " (case-insensitive per APEv2 spec)")
            r[writer] = {"n_fields_before": len(before), "n_fields_after": len(after), "notes": notes,
                         "dropped": d["dropped"], "added": d["added"], "changed": d["changed"],
                         "unexpected_changes": {k: {"before": before[k], "after": after[k]} for k in unexpected},
                         "title_after": after.get("TIT2", after.get("VC:TITLE", after.get("APE:title"))),
                         "audio_unchanged": audio_before == audio_after,
                         "pass": not d["dropped"] and not unexpected and audio_before == audio_after}
            p.unlink()
        res[ext] = r
    return res


def audio_verification(fx: dict[str, Path], work: Path) -> dict:
    """Audio hash must ignore tag edits and catch audio edits, per format."""
    res = {}
    for ext, src in fx.items():
        p = work / f"av{ext}"
        subprocess.run(["cp", "-c", str(src), str(p)], check=True)
        h0 = audio_hash(p)
        h0b = audio_hash(p)
        s0 = sha_file(p)
        apply_edits(p, {"title": "x" * 500})  # tag grows -> audio offset moves
        h1 = audio_hash(p)
        r = {"deterministic": h0 == h0b, "unchanged_after_tag_edit": h0 == h1,
             "file_sha_changed_after_tag_edit": sha_file(p) != s0}
        # audio edit
        if ext in (".mp3", ".flac"):
            q = work / f"av_vol{ext}"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", "volume=0.99",
                            "-map_metadata", "0", str(q)], check=True)
            r["audio_edit"] = "re-encode at volume 0.99"
            r["detects_audio_edit"] = audio_hash(q) != h0
            q.unlink()
        else:
            # no APE encoder: flip one byte in the middle of the audio data
            q = work / f"av_flip{ext}"
            subprocess.run(["cp", "-c", str(src), str(q)], check=True)
            with open(q, "r+b") as fh:
                fh.seek(q.stat().st_size // 2)
                bt = fh.read(1)
                fh.seek(-1, 1)
                fh.write(bytes([bt[0] ^ 0xFF]))
            hq = audio_hash(q)
            r["audio_edit"] = "one byte flipped mid-file (no APE encoder available)"
            r["detects_audio_edit"] = hq != h0
            r["decode_result_after_flip"] = "decode failed" if hq.startswith("DECODE-FAILED") else "decoded, different PCM"
            q.unlink()
        if ext == ".flac":
            from mutagen.flac import FLAC
            r["flac_streaminfo_md5_present"] = FLAC(p).info.md5_signature != 0
        p.unlink()
        r["pass"] = r["deterministic"] and r["unchanged_after_tag_edit"] and r["detects_audio_edit"]
        res[ext] = r
    return res


# =========================================================== side files
def detect_encoding(raw: bytes) -> str:
    from charset_normalizer import from_bytes
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw, cp_isolation=["gb18030", "big5", "shift_jis", "cp1252"]).best()
    return best.encoding if best else "latin-1"


def rewrite_cue(cue: Path, renames: dict[str, str]) -> dict:
    raw = cue.read_bytes()
    enc = detect_encoding(raw)
    text = raw.decode(enc)
    refs = re.findall(r'^\s*FILE\s+"([^"]+)"', text, re.M)

    def sub(m):
        name = m.group(2)
        return f'{m.group(1)}"{renames.get(name, name)}"{m.group(3)}'
    new = re.sub(r'^(\s*FILE\s+)"([^"]+)"(.*)$', sub, text, flags=re.M)
    cue.write_bytes(new.encode(enc))
    return {"encoding_detected": enc, "refs_before": refs,
            "refs_after": re.findall(r'^\s*FILE\s+"([^"]+)"', cue.read_bytes().decode(enc), re.M)}


def side_files(dest: Path, method: str, run: str, jdir: Path) -> dict:
    out = {}
    # ---- (a) real GBK cue + real APE from the NAS sample ----------------
    real_cue = next(NAS_SAMPLE.rglob("*.cue"), None)
    if real_cue:
        src_dir = SCRATCH / "src_cue_album"
        shutil.rmtree(src_dir, ignore_errors=True)
        src_dir.mkdir(parents=True)
        for p in real_cue.parent.iterdir():
            subprocess.run(["cp", "-c", str(p), str(src_dir / p.name)], check=True)
        ape = next(src_dir.glob("*.ape"))
        cue = next(src_dir.glob("*.cue"))
        dalb = dest / "side_cue" / "柏菲 - HIFI年代 - 怀念战友"
        dalb.mkdir(parents=True)
        j = Journal(jdir / f"{run}-cue.jsonl")
        new_ape = dalb / "柏菲 - HIFI年代 - 怀念战友.ape"
        new_cue = dalb / "柏菲 - HIFI年代 - 怀念战友.cue"
        plan_op(j, run, "ape", ape, new_ape, {}, method)
        # relink BEFORE publish: rewrite a local staged copy, then publish it
        # like any other file (published files are never rewritten in place)
        staged = SCRATCH / "staged_real.cue"
        shutil.copy2(cue, staged)
        info = rewrite_cue(staged, {ape.name: new_ape.name})
        plan_op(j, run, "cue", staged, new_cue, {}, method)
        run_all(j)
        ok_dec = new_cue.read_bytes().decode(info["encoding_detected"])
        out["cue_real_gbk"] = {
            **info,
            "refs_resolve": all((dalb / r).exists() for r in info["refs_after"]),
            "still_gbk_encoded": info["encoding_detected"] not in ("utf-8",) and "柏菲" in ok_dec,
            "naive_utf8_read_is_mojibake": "柏菲" not in new_cue.read_bytes().decode("utf-8", "replace"),
        }
    # ---- (b) synthetic album: track + same-stem .lrc + UTF-8 cue ---------
    sdir = SCRATCH / "src_lrc_album"
    shutil.rmtree(sdir, ignore_errors=True)
    sdir.mkdir(parents=True)
    trk = sdir / "01 - old name.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=3",
                    "-c:a", "libmp3lame", str(trk)], check=True)
    (sdir / "01 - old name.lrc").write_text("[00:00.00]歌词 lyric\n", encoding="utf-8")
    (sdir / "album.cue").write_text('TITLE "T"\nFILE "01 - old name.mp3" MP3\n  TRACK 01 AUDIO\n'
                                     '    INDEX 01 00:00:00\n', encoding="utf-8")
    dalb = dest / "side_lrc" / "Artist" / "Album"
    dalb.mkdir(parents=True)
    j = Journal(jdir / f"{run}-lrc.jsonl")
    # side files are ops of their own, planned from the audio op's new stem
    new_stem = "01 - New Title"
    plan_op(j, run, "trk", trk, dalb / f"{new_stem}.mp3", {"title": "New Title"}, method)
    plan_op(j, run, "lrc", sdir / "01 - old name.lrc", dalb / f"{new_stem}.lrc", {}, method)
    # cue: rewrite in a local staging copy BEFORE publishing (no overwrite needed)
    staged_cue = SCRATCH / "staged_album.cue"
    shutil.copy2(sdir / "album.cue", staged_cue)
    info = rewrite_cue(staged_cue, {"01 - old name.mp3": f"{new_stem}.mp3"})
    plan_op(j, run, "cue", staged_cue, dalb / "album.cue", {}, method)
    run_all(j)
    out["lrc_follows_rename"] = {
        "lrc_at_new_stem": (dalb / f"{new_stem}.lrc").exists(),
        "lrc_content_unchanged": (dalb / f"{new_stem}.lrc").read_bytes() == (sdir / "01 - old name.lrc").read_bytes(),
        "no_lrc_at_old_stem": not (dalb / "01 - old name.lrc").exists(),
    }
    out["cue_rewrite_prepublish"] = {**info, "refs_resolve": all((dalb / r).exists() for r in info["refs_after"])}
    out["pass"] = (all(out["lrc_follows_rename"].values()) and out["cue_rewrite_prepublish"]["refs_resolve"]
                   and (not real_cue or (out["cue_real_gbk"]["refs_resolve"] and out["cue_real_gbk"]["still_gbk_encoded"])))
    return out


# =========================================================== scenarios
CRASH_POINTS = ["after_intent", "mid_copy", "after_copy", "after_tag",
                "after_link_before_unlink", "after_publish_syscall", "after_published_journal"]


def make_sources() -> list[Path]:
    d = SCRATCH / "src"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    picks = [next((NAS_SAMPLE / "album_chinese_flac").glob("01*.flac")),
             next((NAS_SAMPLE / "album_clean_mp3_compilation" / "CD 1").glob("01*.mp3")),
             next((NAS_SAMPLE / "singles_chinese_loose").glob("Moon River*"))]
    out = []
    for p in picks:
        q = d / p.name
        subprocess.run(["cp", "-c", str(p), str(q)], check=True)
        out.append(q)
    return out


def invariants(dest_dir: Path, j: Journal) -> dict:
    ops = j.ops()
    temps = [p.name for p in dest_dir.rglob("*") if ".mlt-" in p.name]
    finals = {}
    bad_final = []
    for op, o in ops.items():
        f = Path(o["final"])
        if o.get("ownership") == "created":
            finals[op] = f.exists()
            if f.exists() and sha_file(f) != o["tagged_sha"]:
                bad_final.append(op)
    names = [p.name for p in dest_dir.rglob("*") if p.is_file()]
    return {
        "every_op_terminal": all(o["history"][-1] in TERMINAL for o in ops.values()),
        "terminal_states": {op: o["history"][-1] for op, o in ops.items()},
        "no_temp_files": not temps,
        "temp_files": temps,
        "created_finals_exist": all(finals.values()),
        "no_half_written_final": not bad_final,
        "no_duplicate_names": len(names) == len(set(names)),
    }


def scenario_crash(dest: Path, srcs: list[Path], method: str, jdir: Path) -> dict:
    res = {}
    for cp in CRASH_POINTS:
        if cp == "after_link_before_unlink" and method != "link_unlink":
            res[cp] = {"skipped": f"only applies to link_unlink (method={method})"}
            continue
        run = uuid.uuid4().hex[:6]
        dd = dest / f"crash_{cp}"
        dd.mkdir()
        j = Journal(jdir / f"{run}.jsonl")
        for i, s in enumerate(srcs):
            plan_op(j, run, f"op{i}", s, dd / f"{i:02d} - {s.stem}{s.suffix}", {"title": f"Retitled {i}"}, method)
        pid = os.fork()
        if pid == 0:
            try:
                run_all(j, crash_at=cp, crash_op="op1")
            finally:
                os._exit(0)
        _, status = os.waitpid(pid, 0)
        crashed = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 137
        on_disk_after_crash = sorted(p.name for p in dd.iterdir())
        decisions = recover(j)
        run_all(j)
        inv = invariants(dd, j)
        res[cp] = {"crashed": crashed, "files_after_crash": on_disk_after_crash,
                   "recovery_decisions": decisions, **inv,
                   "all_created": all(o.get("ownership") == "created" for o in j.ops().values()),
                   "pass": crashed and inv["every_op_terminal"] and inv["no_temp_files"]
                   and inv["created_finals_exist"] and inv["no_half_written_final"]
                   and all(o.get("ownership") == "created" for o in j.ops().values())}
    return res


def scenario_awkward(dest: Path, srcs: list[Path], method: str, jdir: Path) -> dict:
    out = {}
    src = srcs[0]

    # a) destination appears mid-run (after tag, before publish)
    run = uuid.uuid4().hex[:6]
    dd = dest / "appears_midrun"
    dd.mkdir()
    j = Journal(jdir / f"{run}.jsonl")
    final = dd / src.name
    plan_op(j, run, "a", src, final, {"title": "Mine"}, method)
    intruder = b"someone else's file"
    run_all(j, hooks={"a": {"before_publish": lambda: final.write_bytes(intruder)}})
    o = j.ops()["a"]
    out["destination_appears_midrun"] = {
        "state": o["history"][-1], "ownership": o.get("ownership"),
        "intruder_untouched": final.read_bytes() == intruder,
        "no_temp_left": not list(dd.glob("*.mlt-*")),
    }
    out["destination_appears_midrun"]["pass"] = (o["history"][-1] == "conflict" and
                                                 out["destination_appears_midrun"]["intruder_untouched"] and
                                                 out["destination_appears_midrun"]["no_temp_left"])

    # b) source changes during the copy (real mutation between chunk 1 and 2)
    run = uuid.uuid4().hex[:6]
    dd = dest / "source_changes"
    dd.mkdir()
    mut = SCRATCH / "mutating_src.flac"
    subprocess.run(["cp", "-c", str(src), str(mut)], check=True)
    j = Journal(jdir / f"{run}.jsonl")
    plan_op(j, run, "b", mut, dd / "out.flac", {}, method)

    def mutate():
        with open(mut, "r+b") as fh:
            fh.seek(mut.stat().st_size - 10)
            fh.write(b"\x00" * 10)
    run_all(j, hooks={"b": {"mid_copy": mutate}})
    o = j.ops()["b"]
    out["source_changes_during_copy"] = {"state": o["history"][-1],
                                         "nothing_published": not (dd / "out.flac").exists(),
                                         "no_temp_left": not list(dd.glob("*.mlt-*"))}
    out["source_changes_during_copy"]["pass"] = (o["history"][-1] == "aborted_source_changed"
                                                 and out["source_changes_during_copy"]["nothing_published"]
                                                 and out["source_changes_during_copy"]["no_temp_left"])

    # c) share drops mid-copy: simulated by EIO/ENOTCONN from the write path,
    #    process then dies; recovery must work from the files alone.
    run = uuid.uuid4().hex[:6]
    dd = dest / "share_drop"
    dd.mkdir()
    j = Journal(jdir / f"{run}.jsonl")
    plan_op(j, run, "c", src, dd / src.name, {"title": "T"}, method)
    pid = os.fork()
    if pid == 0:
        def drop():
            raise OSError(errno.ENOTCONN, "Socket is not connected (simulated SMB drop)")
        try:
            run_all(j, hooks={"c": {"mid_copy": drop}})
        except OSError:
            os._exit(57)
        os._exit(0)
    _, st = os.waitpid(pid, 0)
    left = sorted(p.name for p in dd.iterdir())
    dec = recover(j)
    run_all(j)
    inv = invariants(dd, j)
    out["share_drop_mid_copy"] = {"simulated": True, "child_exit": os.WEXITSTATUS(st),
                                  "files_after_drop": left, "recovery": dec, **inv,
                                  "pass": inv["every_op_terminal"] and inv["no_temp_files"]
                                  and j.ops()["c"].get("ownership") == "created"}

    # d) identical file already at destination
    run = uuid.uuid4().hex[:6]
    dd = dest / "identical_preexisting"
    dd.mkdir()
    pre = dd / src.name
    subprocess.run(["cp", str(src), str(pre)], check=True)
    pre_sha = sha_file(pre)
    j = Journal(jdir / f"{run}.jsonl")
    plan_op(j, run, "d", src, pre, {}, method)
    run_all(j)
    u = undo(j)
    o = j.ops()["d"]
    out["identical_already_exists"] = {"state": o["history"][-1], "ownership": o.get("ownership"),
                                       "undo": u, "still_there_after_undo": pre.exists() and sha_file(pre) == pre_sha}
    out["identical_already_exists"]["pass"] = (o.get("ownership") == "already_existed"
                                               and out["identical_already_exists"]["still_there_after_undo"])

    # e) output edited before undo (tag edit and audio-preserving touch)
    run = uuid.uuid4().hex[:6]
    dd = dest / "edited_before_undo"
    dd.mkdir()
    j = Journal(jdir / f"{run}.jsonl")
    for i, s in enumerate(srcs[:2]):
        plan_op(j, run, f"e{i}", s, dd / s.name, {"title": f"Pub {i}"}, method)
    run_all(j)
    edited = Path(j.ops()["e0"]["final"])
    apply_edits(edited, {"title": "User fixed this by hand"})  # user edit
    u = undo(j)
    out["output_edited_before_undo"] = {
        "undo": u,
        "edited_file_kept": edited.exists(),
        "unedited_file_removed": not Path(j.ops()["e1"]["final"]).exists(),
        "detected_by": "full-file sha256 + inode recorded at publish (tag-only edits keep audio hash equal, so audio hash alone would MISS this)",
        "audio_hash_equal_despite_edit": audio_hash(edited) == j.ops()["e0"]["tagged_audio"],
    }
    out["output_edited_before_undo"]["pass"] = (out["output_edited_before_undo"]["edited_file_kept"]
                                                and out["output_edited_before_undo"]["unedited_file_removed"])
    return out


def scenario_undo_after_crash(dest: Path, srcs: list[Path], method: str, jdir: Path) -> dict:
    """Crash right after publish syscall, recover, then undo: the recovered op
    must be classified 'created' and removed; a pre-existing file must stay."""
    run = uuid.uuid4().hex[:6]
    dd = dest / "undo_after_crash"
    dd.mkdir()
    j = Journal(jdir / f"{run}.jsonl")
    pre = dd / "preexisting.mp3"
    subprocess.run(["cp", str(srcs[1]), str(pre)], check=True)
    plan_op(j, run, "x0", srcs[0], dd / srcs[0].name, {"title": "X"}, method)
    plan_op(j, run, "x1", srcs[1], pre, {}, method)
    pid = os.fork()
    if pid == 0:
        run_all(j, crash_at="after_publish_syscall", crash_op="x0")
        os._exit(0)
    os.waitpid(pid, 0)
    recover(j)
    run_all(j)
    own = {op: o.get("ownership") for op, o in j.ops().items()}
    u = undo(j)
    return {"ownership_before_undo": own, "undo": u,
            "created_removed": not (dd / srcs[0].name).exists(),
            "preexisting_kept": pre.exists(),
            "pass": own == {"x0": "created", "x1": "already_existed"}
            and not (dd / srcs[0].name).exists() and pre.exists()}


# =========================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="scratch destination folder (local or NAS)")
    ap.add_argument("--label", default="local")
    a = ap.parse_args()

    dest = Path(a.dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()) and not (dest / MARKER).exists():
        sys.exit(f"refusing: {dest} is not empty and has no {MARKER} marker")
    # wipe only what this script created before
    if dest.exists():
        for p in dest.iterdir():
            if p.name != MARKER:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MARKER).write_text("created by m0b_v2.py; contents are disposable\n")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    jdir = SCRATCH / "journal"

    t0 = time.time()
    rep = {"dest": str(dest), "label": a.label}
    print("[probe]", flush=True)
    rep["publish_probe"] = probe_publish(dest)
    method = rep["publish_probe"]["chosen_method"]
    srcs = make_sources()
    rep["sources"] = [p.name for p in srcs]
    print(f"[crash] method={method}", flush=True)
    rep["crash_injection"] = scenario_crash(dest, srcs, method, jdir)
    if method == "renamex_excl" and rep["publish_probe"].get("hardlink_new") == "ok":
        print("[crash] also link_unlink", flush=True)
        dl = dest / "link_unlink"
        dl.mkdir()
        rep["crash_injection_link_unlink"] = scenario_crash(dl, srcs, "link_unlink", jdir)
    print("[awkward]", flush=True)
    rep["awkward"] = scenario_awkward(dest, srcs, method, jdir)
    rep["undo_after_crash"] = scenario_undo_after_crash(dest, srcs, method, jdir)
    print("[tags]", flush=True)
    fx = make_tagged_fixtures(SCRATCH / "tagfx")
    work = dest / "tags"
    work.mkdir()
    rep["tag_preservation"] = tag_preservation(fx, work)
    rep["audio_verification"] = audio_verification(fx, work)
    print("[side files]", flush=True)
    rep["side_files"] = side_files(dest, method, uuid.uuid4().hex[:6], jdir)

    s = {
        "publish_method": method,
        "crash_injection_all_pass": all(v.get("pass", True) for v in rep["crash_injection"].values()),
        "crash_injection_link_unlink_all_pass": all(v.get("pass", True) for v in rep.get("crash_injection_link_unlink", {}).values()) if "crash_injection_link_unlink" in rep else None,
        **{f"awkward.{k}": v["pass"] for k, v in rep["awkward"].items()},
        "undo_after_crash": rep["undo_after_crash"]["pass"],
        **{f"tags{ext}.{w}": r["pass"] for ext, rr in rep["tag_preservation"].items() for w, r in rr.items()},
        **{f"audio_hash{ext}": r["pass"] for ext, r in rep["audio_verification"].items()},
        "side_files": rep["side_files"]["pass"],
    }
    rep["summary"] = s
    rep["elapsed_s"] = round(time.time() - t0, 1)
    out = RESULTS / f"m0b_{a.label}_result.json"
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(s, indent=2, ensure_ascii=False))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
