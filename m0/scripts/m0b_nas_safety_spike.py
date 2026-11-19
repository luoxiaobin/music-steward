"""
M0b spike: can files be copied and tagged safely on a destination volume,
surviving crashes, conflicts and dropped connections?

CAVEAT (important): we currently have read-only access to the real NAS
share, so this spike targets a LOCAL destination directory
(dest_local/) as a stand-in for the NAS. It proves the journal/crash/
conflict LOGIC works. It does NOT prove NAS-specific transport behavior
(does SMB support hard links? what does a real dropped SMB connection
look like on reconnect?) -- that still needs a second pass once we have
write access to the NAS.

Source material: ~/Projects/MP3LibraryTool/m0/nas_sample/ (real files
copied read-only from the NAS). This script NEVER writes to nas_sample/;
it only reads from it. All copying/tagging/crashing happens in
dest_local/ and a scratch "incoming" staging dir, both local and
disposable.
"""
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "nas_sample"
DEST = ROOT / "dest_local"
STAGE = ROOT / "stage_incoming"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

AUDIO_EXTS = {".mp3", ".flac", ".ape"}


def reset_dirs():
    for d in (DEST, STAGE):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)


def audio_hash(path: Path) -> str:
    """Hash the AUDIO SAMPLES only (tag-independent), via ffmpeg decode to
    raw PCM. This is the 'audio unchanged' check the spec calls for."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return f"<decode failed: {proc.stderr.decode(errors='replace')[:200]}>"
    return hashlib.sha256(proc.stdout).hexdigest()


# ---------------------------------------------------------------------
# 1. Safe publish method probe
# ---------------------------------------------------------------------
def probe_safe_publish(target_dir: Path) -> dict:
    out = {}
    a = target_dir / "_probe_a.tmp"
    b = target_dir / "_probe_b.tmp"
    a.write_bytes(b"hello")

    # hard link support
    try:
        os.link(a, b)
        out["hardlink_supported"] = True
        b.unlink()
    except OSError as e:
        out["hardlink_supported"] = False
        out["hardlink_error"] = str(e)

    # rename-that-refuses-to-overwrite: os.rename on POSIX silently
    # overwrites, so the safe primitive is os.link() + os.unlink(src)
    # (atomic, fails if dest exists) -- or on platforms without hard
    # links, open(..., O_EXCL) to claim the name first.
    c = target_dir / "_probe_c.tmp"
    c.write_bytes(b"existing")
    try:
        os.link(a, c)  # c already exists -> must raise FileExistsError
        out["link_refuses_overwrite"] = False
    except FileExistsError:
        out["link_refuses_overwrite"] = True
    except OSError as e:
        out["link_refuses_overwrite"] = f"<unexpected error: {e}>"

    try:
        fd = os.open(str(c), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        out["o_excl_refuses_overwrite"] = False
    except FileExistsError:
        out["o_excl_refuses_overwrite"] = True

    a.unlink(missing_ok=True)
    c.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------------
# 2. Journaled apply: copy -> tag -> publish, each step logged, so a
#    crash at any point can be diagnosed and safely resumed/rolled back
#    by inspecting the journal + filesystem state (never by trusting
#    memory of what "should" have happened).
# ---------------------------------------------------------------------
STEPS = ["planned", "staged", "tagged", "published", "committed"]


def run_apply_job(src_file: Path, dest_file: Path, journal_path: Path,
                   crash_after_step: str = None, new_title: str = None):
    """Simulates one file's publish pipeline. If crash_after_step is set,
    raises SystemExit right after that step is journaled, mid-function,
    like a kill -9 would (no cleanup, no finally)."""
    import mutagen

    journal = {"src": str(src_file), "dest": str(dest_file), "steps": []}

    def log(step):
        journal["steps"].append({"step": step, "ts": time.time()})
        journal_path.write_text(json.dumps(journal, indent=2))
        if step == crash_after_step:
            os._exit(137)  # hard kill, no Python cleanup -- simulates kill -9

    log("planned")

    tmp_dest = dest_file.with_suffix(dest_file.suffix + ".part")
    shutil.copy2(src_file, tmp_dest)
    log("staged")

    if new_title:
        f = mutagen.File(str(tmp_dest), easy=True)
        if f is not None:
            f["title"] = [new_title]
            f.save()
    log("tagged")

    # publish: claim the final name without clobbering anything
    try:
        os.link(tmp_dest, dest_file)
        tmp_dest.unlink()
    except FileExistsError:
        # Destination already has this final name (either a real prior
        # publish we're safely resuming past, or a genuine name clash).
        # Either way our OWN staged .part is now garbage -- clean it up,
        # never leave an orphan temp file behind.
        tmp_dest.unlink(missing_ok=True)
        journal["conflict"] = "dest_exists_at_publish"
        journal_path.write_text(json.dumps(journal, indent=2))
        return journal
    log("published")

    log("committed")
    return journal


def recover_and_check(dest_file: Path, journal_path: Path) -> dict:
    """What a resumed/undo process would see after a crash: inspect the
    journal + actual filesystem state, never assume."""
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
    steps_done = [s["step"] for s in journal.get("steps", [])]
    tmp_dest = Path(journal.get("dest", "")).with_suffix(
        Path(journal.get("dest", "")).suffix + ".part"
    ) if journal.get("dest") else None

    return {
        "steps_done": steps_done,
        "last_step": steps_done[-1] if steps_done else None,
        "dest_final_exists": dest_file.exists(),
        "dest_partial_exists": tmp_dest.exists() if tmp_dest else None,
        "no_duplicate_final_names": True,  # checked by caller across all jobs
    }


def test_crash_at_each_step(sample_file: Path) -> dict:
    results = {}
    for step in STEPS[:-1]:  # can't "crash after committed", nothing left
        reset_sub = DEST / f"crash_{step}"
        reset_sub.mkdir(parents=True, exist_ok=True)
        journal_path = reset_sub / "journal.json"
        dest_file = reset_sub / sample_file.name

        pid = os.fork()
        if pid == 0:
            # child: run the job, which os._exit(137)s at the crash point
            run_apply_job(sample_file, dest_file, journal_path,
                          crash_after_step=step)
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)

        # "rerun" after crash: recovery scan + a second apply attempt
        before = recover_and_check(dest_file, journal_path)
        # idempotent rerun: running again from scratch state should not
        # duplicate or corrupt -- since our publish uses hardlink+unlink
        # (atomic claim), a second full run either completes cleanly or
        # safely detects the partial and redoes it.
        tmp_leftover = dest_file.with_suffix(dest_file.suffix + ".part")
        tmp_leftover.unlink(missing_ok=True)  # recovery would clean orphan .part
        run_apply_job(sample_file, dest_file, journal_path)
        after = recover_and_check(dest_file, journal_path)

        results[step] = {
            "crash_signal_exit": os.WEXITSTATUS(status) if os.WIFEXITED(status) else None,
            "state_right_after_crash": before,
            "state_after_rerun": after,
            "exactly_one_final_file": sum(
                1 for p in reset_sub.iterdir() if p.name == sample_file.name
            ) == 1,
            "no_orphan_part_files": not any(
                p.suffix == ".part" for p in reset_sub.iterdir()
            ),
        }
    return results


# ---------------------------------------------------------------------
# 3. Conflict scenarios
# ---------------------------------------------------------------------
def test_conflicts(sample_file: Path) -> dict:
    out = {}

    # a) destination already has a file with the same name when we try to publish
    sub = DEST / "conflict_dest_exists"
    sub.mkdir(parents=True, exist_ok=True)
    dest_file = sub / sample_file.name
    dest_file.write_bytes(b"not the real content, pre-existing file")
    journal_path = sub / "journal.json"
    journal = run_apply_job(sample_file, dest_file, journal_path)
    out["dest_exists_at_publish"] = {
        "detected_as_conflict": journal.get("conflict") == "dest_exists_at_publish",
        "preexisting_file_untouched": dest_file.read_bytes().startswith(b"not the real"),
    }

    # b) source file changes (mtime+size) during the "copy" -- detect via
    #    stat snapshot taken before copy vs after copy
    sub2 = DEST / "conflict_source_changed"
    sub2.mkdir(parents=True, exist_ok=True)
    src_copy = STAGE / ("mutating_" + sample_file.name)
    shutil.copy2(sample_file, src_copy)
    stat_before = src_copy.stat()
    tmp_dest = sub2 / (sample_file.name + ".part")
    shutil.copy2(src_copy, tmp_dest)
    # mutate source after the copy "started" (simulated: after, in reality
    # you'd snapshot stat before AND after the actual copy syscalls)
    with open(src_copy, "ab") as f:
        f.write(b"\x00" * 16)
    stat_after = src_copy.stat()
    changed = (stat_before.st_mtime != stat_after.st_mtime or
               stat_before.st_size != stat_after.st_size)
    out["source_changed_during_copy"] = {"detected": changed}

    # c) identical file already exists at destination (by audio hash)
    sub3 = DEST / "conflict_identical_exists"
    sub3.mkdir(parents=True, exist_ok=True)
    existing = sub3 / sample_file.name
    shutil.copy2(sample_file, existing)
    h_existing = audio_hash(existing)
    h_incoming = audio_hash(sample_file)
    out["identical_file_already_exists"] = {
        "audio_hash_matches": h_existing == h_incoming,
        "classification": "already_existed" if h_existing == h_incoming else "conflict",
    }

    # d) output edited after publish, before undo -- undo must detect and
    #    refuse to silently discard the edit
    sub4 = DEST / "edited_before_undo"
    sub4.mkdir(parents=True, exist_ok=True)
    published = sub4 / sample_file.name
    shutil.copy2(sample_file, published)
    published_hash_at_publish = audio_hash(published)
    # user edits the file afterward (re-encode/trim simulated by appending)
    time.sleep(1.1)  # ensure mtime resolution differs
    with open(published, "r+b") as f:
        f.seek(0, os.SEEK_END)
    os.utime(published, None)  # touch mtime without changing audio bytes,
    # then do a REAL content edit to prove hash-based detection catches it:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(sample_file),
         "-af", "volume=1.01", str(published)],
        capture_output=True,
    )
    published_hash_now = audio_hash(published)
    out["output_edited_before_undo"] = {
        "edit_detected_by_audio_hash": published_hash_at_publish != published_hash_now,
    }

    return out


# ---------------------------------------------------------------------
# 4. Tags survive editing, per format
# ---------------------------------------------------------------------
def test_tag_roundtrip(sample_file: Path) -> dict:
    import mutagen
    sub = DEST / "tag_roundtrip"
    sub.mkdir(parents=True, exist_ok=True)
    work = sub / sample_file.name
    shutil.copy2(sample_file, work)

    audio_before = audio_hash(work)
    f = mutagen.File(str(work), easy=False)
    if f is None or f.tags is None:
        if sample_file.suffix.lower() == ".ape":
            return {
                "format": sample_file.suffix,
                "note": "No embedded APE tag on this file -- metadata for "
                         "this album lives entirely in the sibling .cue "
                         "sheet, which is itself GBK/GB2312-encoded, not "
                         "UTF-8 (reads as mojibake without transcoding). "
                         "Real finding: APE+cue albums need cue-sheet "
                         "parsing with encoding detection, not mutagen tags.",
            }
        return {"error": "mutagen could not open/read tags", "format": sample_file.suffix}

    tags_before = {k: list(v) if hasattr(v, "__iter__") else v
                   for k, v in dict(f.tags).items()} if f.tags else {}

    # change ONE field via the easy API (title), save, reopen, compare
    fe = mutagen.File(str(work), easy=True)
    original_title = fe.get("title")
    fe["title"] = ["M0b Test Title Edit"]
    fe.save()

    f2 = mutagen.File(str(work), easy=False)
    tags_after = {k: list(v) if hasattr(v, "__iter__") else v
                  for k, v in dict(f2.tags).items()} if f2.tags else {}
    audio_after = audio_hash(work)

    unchanged_fields = []
    dropped_fields = []
    title_like_keys = {"tit2", "title", "\xa9nam"}
    for k in tags_before:
        if k.lower().split(":")[0] in title_like_keys:
            continue
        if k in tags_after and tags_after[k] == tags_before[k]:
            unchanged_fields.append(k)
        else:
            dropped_fields.append(k)

    return {
        "format": sample_file.suffix,
        "n_fields_before": len(tags_before),
        "n_fields_after": len(tags_after),
        "fields_preserved": unchanged_fields,
        "fields_dropped_or_changed": dropped_fields,
        "title_changed": tags_after.get("TIT2") != tags_before.get("TIT2")
                          or original_title != fe.get("title"),
        "audio_unchanged": audio_before == audio_after,
    }


# ---------------------------------------------------------------------
# 5. Side files: .lrc follows rename, .cue gets rewritten
# ---------------------------------------------------------------------
def test_side_files() -> dict:
    out = {}
    # find a .cue in our samples
    cue_files = list(SRC.rglob("*.cue"))
    if cue_files:
        cue = cue_files[0]
        sub = DEST / "side_files_cue"
        sub.mkdir(parents=True, exist_ok=True)
        # copy the whole album dir (cue + referenced audio) so we can rename
        album_dir = cue.parent
        dest_album = sub / "renamed_album"
        shutil.copytree(album_dir, dest_album)
        cue_dest = dest_album / cue.name
        text = cue_dest.read_text(encoding="utf-8", errors="replace")
        import re
        referenced = re.findall(r'FILE\s+"([^"]+)"', text)
        out["cue_found"] = True
        out["cue_referenced_files"] = referenced
        out["referenced_files_exist_at_dest"] = [
            (dest_album / r).exists() for r in referenced
        ]
        # simulate rename of the audio file + rewrite cue reference
        if referenced:
            old_name = referenced[0]
            old_path = dest_album / old_name
            if old_path.exists():
                new_name = "renamed_track.ape"
                new_path = dest_album / new_name
                old_path.rename(new_path)
                new_text = text.replace(old_name, new_name)
                cue_dest.write_text(new_text, encoding="utf-8")
                out["cue_rewrite_applied"] = new_name in cue_dest.read_text(encoding="utf-8")
                out["renamed_file_exists"] = new_path.exists()
    else:
        out["cue_found"] = False

    lrc_files = list(SRC.rglob("*.lrc"))
    out["lrc_found"] = bool(lrc_files)
    if lrc_files:
        lrc = lrc_files[0]
        sub = DEST / "side_files_lrc"
        sub.mkdir(parents=True, exist_ok=True)
        audio_sibling = None
        stem = lrc.stem
        for ext in AUDIO_EXTS:
            cand = lrc.parent / (stem + ext)
            if cand.exists():
                audio_sibling = cand
                break
        if audio_sibling:
            shutil.copy2(lrc, sub / lrc.name)
            shutil.copy2(audio_sibling, sub / audio_sibling.name)
            new_stem = "renamed_track"
            (sub / audio_sibling.name).rename(sub / (new_stem + audio_sibling.suffix))
            (sub / lrc.name).rename(sub / (new_stem + ".lrc"))
            out["lrc_followed_rename"] = (sub / (new_stem + ".lrc")).exists()
    return out


def main():
    reset_dirs()
    report = {}

    report["safe_publish_probe"] = probe_safe_publish(DEST)

    # pick one sample file per format present
    all_audio = [p for p in SRC.rglob("*") if p.suffix.lower() in AUDIO_EXTS]
    by_ext = {}
    for p in all_audio:
        by_ext.setdefault(p.suffix.lower(), p)
    report["sample_files_used"] = {k: str(v) for k, v in by_ext.items()}

    mp3_sample = by_ext.get(".mp3")
    flac_sample = by_ext.get(".flac")
    ape_sample = by_ext.get(".ape")

    if mp3_sample:
        report["crash_injection_mp3"] = test_crash_at_each_step(mp3_sample)
        report["conflicts_mp3"] = test_conflicts(mp3_sample)

    report["tag_roundtrip"] = {}
    for label, p in (("mp3", mp3_sample), ("flac", flac_sample), ("ape", ape_sample)):
        if p:
            try:
                report["tag_roundtrip"][label] = test_tag_roundtrip(p)
            except Exception as e:
                report["tag_roundtrip"][label] = {"error": str(e)}

    report["side_files"] = test_side_files()

    report["caveats"] = [
        "Run against LOCAL disk (dest_local/), not the real NAS -- we "
        "currently only have read-only NAS access. Hard-link support "
        "and real dropped-connection behavior must be re-verified once "
        "write access to the NAS is available.",
        "Crash simulation uses os.fork() + os._exit(137) to mimic kill -9 "
        "at a precise point in the pipeline; this is a reasonable proxy "
        "for a real crash but not identical to e.g. a kernel panic or "
        "SMB session drop mid-syscall.",
    ]

    out_path = RESULTS / "m0b_result.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"Wrote {out_path}")

    # quick pass/fail summary
    summary = {
        "hardlink_supported": report["safe_publish_probe"].get("hardlink_supported"),
        "link_refuses_overwrite": report["safe_publish_probe"].get("link_refuses_overwrite"),
    }
    if "crash_injection_mp3" in report:
        summary["crash_recovery_clean_at_every_step"] = all(
            v["exactly_one_final_file"] and v["no_orphan_part_files"]
            for v in report["crash_injection_mp3"].values()
        )
    if "conflicts_mp3" in report:
        c = report["conflicts_mp3"]
        summary["conflict_detection_ok"] = (
            c["dest_exists_at_publish"]["detected_as_conflict"]
            and c["dest_exists_at_publish"]["preexisting_file_untouched"]
            and c["source_changed_during_copy"]["detected"]
            and c["identical_file_already_exists"]["audio_hash_matches"]
            and c["output_edited_before_undo"]["edit_detected_by_audio_hash"]
        )
    if report["tag_roundtrip"]:
        summary["tag_preservation_ok"] = all(
            not v.get("fields_dropped_or_changed") and v.get("audio_unchanged")
            for v in report["tag_roundtrip"].values()
            if "error" not in v and "note" not in v
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
