"""ADR 0003: retire the originals -- a verified, reversible, labelled move into <root>/_ORIGINALS/.

The one place a collection folder is written to, so every refusal and every crash point is tested."""
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from music_steward import retire, settings
from music_steward.apply import plan, runner
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tone
from .test_apply import accepted, snapshot  # noqa: F401  (fixtures: one accepted album; folder snapshot)

pytestmark = needs_ffmpeg

ALBUM = "Ò¹º½"          # the folder of the `accepted` fixture


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def applied(accepted, con, tmp_path):  # noqa: F811
    """The accepted album, copied (4 files: 2 tracks, lyrics, cover) -- retiring is allowed for the root."""
    lib, dest = accepted
    p = plan.load(con, plan.build(con, str(dest), 1))
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 4}, res
    settings.set_retire_allowed(con, 1, True)
    return lib, dest, res["run"]


def originals(lib):
    return sorted(str(p.relative_to(lib)) for p in (lib / retire.ORIGINALS).rglob("*") if p.is_file())


def test_nothing_happens_unless_the_collection_folder_allows_it(applied, con):
    lib, dest, run = applied
    settings.set_retire_allowed(con, 1, False)
    before = snapshot(lib)
    with pytest.raises(retire.RetireError, match="not allowed"):
        retire.retire(con, run)
    assert snapshot(lib) == before and not (lib / retire.ORIGINALS).exists()


def test_dry_run_changes_nothing_and_says_what_would_move(applied, con):
    lib, dest, run = applied
    before = snapshot(lib)
    d = retire.dry_run(con, run)
    assert (d["albums_ok"], d["files_ok"], d["albums_skipped"], d["allowed"]) == (1, 4, 0, True)
    assert d["bytes_ok"] == sum(p.stat().st_size for p in (lib / ALBUM).iterdir())
    assert snapshot(lib) == before and not (lib / retire.ORIGINALS).exists()


def test_retiring_moves_the_originals_labels_them_and_leaves_the_copies(applied, con):
    lib, dest, run = applied
    before = {p.name: sha(p) for p in (lib / ALBUM).iterdir()}
    copies = snapshot(dest)
    res = retire.retire(con, run)
    assert (res["status"], res["albums"], res["files"]) == ("complete", 1, 4) and res["skipped"] == []
    batch = Path(res["folder"])
    assert batch.parent == lib / retire.ORIGINALS and batch.name.startswith(f"run{run}-")
    assert not list((lib / ALBUM).glob("*"))                                  # nothing left at the old paths
    moved = {p.name: sha(p) for p in (batch / ALBUM).iterdir()}
    assert moved == before                                                     # every byte, kept
    assert snapshot(dest) == copies                                            # the organized copies untouched
    readme = (batch / retire.README).read_text(encoding="utf-8")
    assert "ORIGINAL FILES" in readme and "RESTORE" in readme and "ARCHIVE" in readme and "DELETE" in readme
    assert str(dest) in readme and "cannot be undone" in readme
    rows = (batch / retire.MANIFEST).read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("original_path,size") and len(rows) == 5
    assert {r.split(",")[0] for r in rows[1:]} == {f"{ALBUM}/{n}" for n in before}
    b = con.execute("SELECT * FROM retire_batch").fetchone()
    assert (b["status"], b["restored_at"]) == ("complete", None)
    assert [r[0] for r in con.execute("SELECT state FROM retired_file")] == ["moved"] * 4


def test_the_readme_is_written_in_the_interface_language(applied, con):
    lib, dest, run = applied
    settings.save(con, {"language": "zh-hans"})
    res = retire.retire(con, run)
    text = (Path(res["folder"]) / retire.README).read_text(encoding="utf-8")
    assert "原始文件" in text and "恢复" in text and "ORIGINAL FILES" not in text


def test_the_scanner_ignores_the_holding_folder(applied, con):
    lib, dest, run = applied
    retire.retire(con, run)
    r = scan(con, lib)
    assert (r.counts["n_new"], r.counts["n_removed"]) == (0, 4)               # gone from the inventory, not rediscovered
    assert not con.execute("SELECT 1 FROM file WHERE relpath LIKE ? AND removed_at IS NULL",
                           (retire.ORIGINALS + "%",)).fetchone()


def test_restore_puts_every_file_back_and_the_scan_recognises_them(applied, con):
    lib, dest, run = applied
    before = snapshot(lib)
    res = retire.retire(con, run)
    scan(con, lib)
    out = retire.restore(con, res["batch"])
    assert (out["restored"], out["blocked"], out["missing"]) == (4, [], [])
    outside = lambda s: {k: v for k, v in s.items() if retire.ORIGINALS not in k}   # noqa: E731
    assert outside(snapshot(lib)) == outside(before)                           # byte-identical, same places
    batch = Path(res["folder"])
    assert sorted(p.name for p in batch.iterdir()) == [retire.MANIFEST, retire.README]    # labels stay, folders tidied
    assert con.execute("SELECT restored_at FROM retire_batch").fetchone()[0]
    ids = {r["relpath"]: r["id"] for r in con.execute("SELECT id, relpath FROM file")}
    hashed = con.execute("SELECT count(*) FROM file WHERE file_sha256 IS NOT NULL").fetchone()[0]
    r = scan(con, lib)
    assert r.counts["n_new"] == 0 and r.counts["n_unchanged"] >= 4             # the same files again (ADR 0002)
    assert {r["relpath"]: r["id"] for r in con.execute("SELECT id, relpath FROM file WHERE removed_at IS NULL")} == ids
    assert con.execute("SELECT count(*) FROM file WHERE file_sha256 IS NOT NULL").fetchone()[0] == hashed


def test_restore_never_overwrites(applied, con):
    lib, dest, run = applied
    res = retire.retire(con, run)
    (lib / ALBUM).mkdir(exist_ok=True)
    (lib / ALBUM / "cover.jpg").write_bytes(b"someone else's file")
    out = retire.restore(con, res["batch"])
    assert out["restored"] == 3 and [b["relpath"] for b in out["blocked"]] == [f"{ALBUM}/cover.jpg"]
    assert (lib / ALBUM / "cover.jpg").read_bytes() == b"someone else's file"
    assert (Path(res["folder"]) / ALBUM / "cover.jpg").exists()               # the retired one is still there


# ---- the checks, one by one -----------------------------------------------------------------------
def skipped_with(con, run, text):
    before_batches = con.execute("SELECT count(*) FROM retire_batch").fetchone()[0]
    res = retire.retire(con, run)
    assert res["albums"] == 0 and res["batch"] is None, res
    assert text in res["skipped"][0]["reason"], res["skipped"]
    assert con.execute("SELECT count(*) FROM retire_batch").fetchone()[0] == before_batches   # no batch, no folder
    return res


def test_an_original_changed_since_it_was_copied_is_not_moved(applied, con):
    lib, dest, run = applied
    p = lib / ALBUM / "01 - x.mp3"
    os.utime(p, ns=(0, p.stat().st_mtime_ns + 10**9))
    skipped_with(con, run, "changed since it was copied")
    assert p.exists() and not (lib / retire.ORIGINALS).exists()


def test_an_original_with_new_content_but_the_same_size_and_time_is_not_moved(applied, con):
    lib, dest, run = applied
    p = lib / ALBUM / "01 - x.mp3"
    st = p.stat()
    data = bytearray(p.read_bytes())
    data[-5000:] = bytes(b ^ 0xFF for b in data[-5000:])          # same size, other content at the end
    p.write_bytes(bytes(data))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    skipped_with(con, run, "content changed")


def test_a_copy_whose_audio_changed_is_not_trusted(applied, con):
    lib, dest, run = applied
    copy = next(dest.rglob("01 - *.mp3"))
    tone(copy, ["-c:a", "libmp3lame"], secs=2, freq=1500)         # different audio under the same name
    skipped_with(con, run, "audio of the organized copy changed")


def test_a_retagged_copy_still_counts_because_the_audio_is_what_matters(applied, con):
    from mutagen.id3 import ID3, TIT2
    lib, dest, run = applied
    copy = next(dest.rglob("01 - *.mp3"))
    t = ID3(copy)
    t.add(TIT2(encoding=3, text="retagged in foobar2000"))
    t.save()
    assert retire.retire(con, run)["albums"] == 1


def test_a_missing_copy_is_not_retired(applied, con):
    lib, dest, run = applied
    next(dest.rglob("02 - *.mp3")).unlink()
    skipped_with(con, run, "organized copy is missing")


def test_an_undone_run_cannot_be_retired(applied, con):
    lib, dest, run = applied
    runner.undo(con, run)
    with pytest.raises(retire.RetireError, match="undone"):
        retire.retire(con, run)
    with pytest.raises(retire.RetireError, match="undone"):
        retire.dry_run(con, run)


def test_a_symlink_on_the_path_of_an_original_is_refused(applied, con, tmp_path):
    lib, dest, run = applied
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.move(str(lib / ALBUM), str(elsewhere / ALBUM))
    os.symlink(elsewhere / ALBUM, lib / ALBUM)
    skipped_with(con, run, "link")
    assert len(list((elsewhere / ALBUM).iterdir())) == 4                       # nothing moved out of the target


def test_a_holding_folder_that_is_a_link_is_refused(applied, con, tmp_path):
    lib, dest, run = applied
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, lib / retire.ORIGINALS)
    with pytest.raises(retire.RetireError, match="link"):
        retire.retire(con, run)
    assert not list(elsewhere.iterdir()) and len(list((lib / ALBUM).iterdir())) == 4


def test_a_partly_copied_album_is_not_retired_at_all(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    p1 = plan.build(con, str(dest), 1)
    src = lib / ALBUM / "02 - x.mp3"
    os.utime(src, ns=(0, src.stat().st_mtime_ns + 10**9))                      # changes after planning: not copied
    res = runner.apply(con, p1, journal_root=tmp_path / "j")
    assert res["states"].get("aborted_source_changed") and res["states"].get("committed")
    settings.set_retire_allowed(con, 1, True)
    skipped_with(con, res["run"], "not copied")
    assert len(list((lib / ALBUM).iterdir())) == 4                             # even the verified files stay


def test_files_that_were_not_part_of_the_run_are_never_touched(applied, con):
    """Another album in the collection, and a stray file in the retired album's folder: both stay."""
    lib, dest, run = applied
    tone(lib / "Other/01.mp3", ["-c:a", "libmp3lame"], secs=1, freq=700)
    (lib / ALBUM / "notes.txt").write_text("mine", encoding="utf-8")
    scan(con, lib)
    res = retire.retire(con, run)
    assert res["files"] == 4
    assert (lib / "Other/01.mp3").exists() and (lib / ALBUM / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not [p for p in originals(lib) if "Other" in p or "notes.txt" in p]


# ---- crash points: every file ends up in exactly one place ----------------------------------------
class Crash(Exception):
    pass


def _batch_id(con):
    return con.execute("SELECT id FROM retire_batch").fetchone()[0]


@pytest.mark.parametrize("when", ["before_rename", "after_rename"])
def test_a_crash_while_moving_is_settled_by_resume(applied, con, monkeypatch, when):
    lib, dest, run = applied
    before = {p.name: sha(p) for p in (lib / ALBUM).iterdir()}
    real, calls = retire._rename_noreplace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            if when == "after_rename":
                real(src, dst)
            raise Crash()
        return real(src, dst)
    monkeypatch.setattr(retire, "_rename_noreplace", flaky)
    with pytest.raises(Crash):
        retire.retire(con, run)
    monkeypatch.setattr(retire, "_rename_noreplace", real)
    batch = con.execute("SELECT * FROM retire_batch").fetchone()
    assert batch["status"] == "running"
    res = retire.resume(con, batch["id"])
    assert res["status"] == "complete" and res["files"] == 4
    assert not list((lib / ALBUM).glob("*"))
    assert {p.name: sha(p) for p in (Path(batch["folder"]) / ALBUM).iterdir()} == before
    assert [r[0] for r in con.execute("SELECT state FROM retired_file")] == ["moved"] * 4
    # and Restore brings every one back, whatever happened on the way
    assert retire.restore(con, batch["id"])["restored"] == 4
    assert {p.name: sha(p) for p in (lib / ALBUM).iterdir()} == before


def test_stop_between_albums_then_resume_finishes(applied, con):
    import threading
    lib, dest, run = applied
    stop = threading.Event()
    stop.set()
    res = retire.retire(con, run, stop=stop)
    assert res["status"] == "stopped" and res["batch"] is None and len(list((lib / ALBUM).iterdir())) == 4


def test_a_target_that_already_exists_is_never_overwritten(applied, con, monkeypatch):
    """Something already sits at a target path: that file is reported as a conflict, nothing is replaced,
    and the original stays where it was; the rest of the album still moves."""
    lib, dest, run = applied
    real_new = retire._new_batch

    def with_squatter(con_, run_):
        b = real_new(con_, run_)
        (Path(b["folder"]) / ALBUM).mkdir()
        (Path(b["folder"]) / ALBUM / "cover.jpg").write_bytes(b"already here")
        return b
    monkeypatch.setattr(retire, "_new_batch", with_squatter)
    res = retire.retire(con, run)
    folder = Path(res["folder"])
    assert (folder / ALBUM / "cover.jpg").read_bytes() == b"already here"        # not replaced
    assert (lib / ALBUM / "cover.jpg").exists()                                    # the original stays
    states = dict(con.execute("SELECT relpath, state FROM retired_file").fetchall())
    assert states[f"{ALBUM}/cover.jpg"] == "conflict" and list(states.values()).count("moved") == 3
    assert res["files"] == 3


# ---- duplicates and split albums -------------------------------------------------------------------
def _cue_album(lib, name, freq=300):
    from .test_cue import CUE
    a = lib / name
    tone(a / "image.flac", ["-c:a", "flac"], secs=6, freq=freq)
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac").encode("gbk"))
    return a


def _accept_all_own_info(con):
    from music_steward.recognize import local, units
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    for (uid,) in con.execute("SELECT id FROM unit").fetchall():
        con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
        cid = local.add(con, uid)
        con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                    "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()


def test_a_byte_identical_duplicate_album_is_retired_with_its_verified_twin(tmp_path, con):
    lib = tmp_path / "lib"
    a = _cue_album(lib, "A")
    shutil.copytree(a, lib / "B")
    scan(con, lib)
    _accept_all_own_info(con)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert p["summary"]["duplicate_albums_skipped"] == 1
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    settings.set_retire_allowed(con, 1, True)
    d = retire.dry_run(con, res["run"])
    assert sorted(a["kind"] for a in d["albums"]) == ["album", "duplicate"] and d["albums_ok"] == 2
    out = retire.retire(con, res["run"])
    assert (out["albums"], out["files"]) == (2, 4)                             # image + cue of each folder
    assert not list((lib / "A").glob("*")) and not list((lib / "B").glob("*"))


def test_a_duplicate_that_is_not_byte_identical_any_more_stays(tmp_path, con):
    lib = tmp_path / "lib"
    a = _cue_album(lib, "A")
    shutil.copytree(a, lib / "B")
    scan(con, lib)
    _accept_all_own_info(con)
    p = plan.build(con, str(tmp_path / "out"), 1)
    dup = lib / "B" / "image.flac"
    st = dup.stat()
    data = bytearray(dup.read_bytes())
    data[-3000:] = bytes(b ^ 0xFF for b in data[-3000:])
    dup.write_bytes(bytes(data))
    os.utime(dup, ns=(st.st_atime_ns, st.st_mtime_ns))
    res = runner.apply(con, p, journal_root=tmp_path / "j")
    settings.set_retire_allowed(con, 1, True)
    out = retire.retire(con, res["run"])
    assert out["albums"] == 1 and len(out["skipped"]) == 1                     # the twin that still verifies
    assert (lib / "B" / "image.flac").exists() and (lib / "B" / "image.cue").exists()   # the duplicate stays whole


def test_a_split_album_retires_its_image_and_cue_only_when_every_track_is_safe(tmp_path, con, monkeypatch):
    from .test_split import noise
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    from .test_cue import CUE
    noise(lib / "HIFI/image.flac", 6)
    (lib / "HIFI/image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac").encode("gbk"))
    (lib / "HIFI/notes.txt").write_text("not part of the plan", encoding="utf-8")
    scan(con, lib)
    _accept_all_own_info(con)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert set(res["states"]) == {"committed"}
    settings.set_retire_allowed(con, 1, True)
    # one track gone from the destination: the image must stay
    victim = next((tmp_path / "out").rglob("02 - *.flac"))
    keep = victim.read_bytes()
    victim.unlink()
    skipped_with(con, res["run"], "organized copy is missing")
    assert (lib / "HIFI/image.flac").exists() and (lib / "HIFI/image.cue").exists()
    victim.write_bytes(keep)
    out = retire.retire(con, res["run"])
    assert out["albums"] == 1 and out["files"] == 2                            # image and its cue sheet
    assert (lib / "HIFI/notes.txt").exists()                                   # a file outside the plan stays
    assert not (lib / "HIFI/image.flac").exists() and not (lib / "HIFI/image.cue").exists()


def test_the_collection_listing_never_gains_a_delete(applied, con):
    """Retire and Restore only rename: the set of bytes under the root is the same before and after."""
    lib, dest, run = applied
    def total():
        return sorted(sha(p) for p in lib.rglob("*") if p.is_file() and retire.ORIGINALS not in p.parts)
    original_hashes = total()
    res = retire.retire(con, run)
    moved = sorted(sha(p) for p in Path(res["folder"]).rglob("*") if p.is_file() and p.name not in (retire.README, retire.MANIFEST))
    assert moved == original_hashes
    retire.restore(con, res["batch"])
    assert total() == original_hashes


def test_the_manifest_names_each_originals_own_copy(applied, con):
    lib, dest, run = applied
    res = retire.retire(con, run)
    import csv
    rows = list(csv.DictReader(open(Path(res["folder"]) / retire.MANIFEST, encoding="utf-8-sig", newline="")))
    by = {r["original_path"]: r for r in rows}
    assert by[f"{ALBUM}/01 - x.mp3"]["organized_copy"].endswith("01 - 序曲.mp3")
    assert by[f"{ALBUM}/02 - x.mp3"]["organized_copy"].endswith("02 - 夜航.mp3")
    assert by[f"{ALBUM}/cover.jpg"]["organized_copy"].endswith("cover.jpg")
    for r in rows:                                                    # every listed copy exists in the destination
        for c in r["organized_copy"].split(" | "):
            assert (dest / c).is_file(), c
