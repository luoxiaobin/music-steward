"""ADR 0003: Archive (a verified copy of the parked originals) and Delete (free the space, once the user is sure).

Delete is irreversible, so every refusal, every crash point and every link trick is tested."""
import csv
import hashlib
import os
import shutil
import threading
from pathlib import Path

import pytest

from music_steward import retire, settings
from music_steward.apply import plan, runner
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tone
from .test_apply import accepted, snapshot  # noqa: F401  (fixtures)
from .test_retire import ALBUM, Crash, _accept_all_own_info, _cue_album, applied, sha  # noqa: F401

pytestmark = needs_ffmpeg


@pytest.fixture
def parked(applied, con, tmp_path):  # noqa: F811
    """The applied album, set aside: returns (lib, dest, run, batch row, batch folder). Cooling-off off."""
    lib, dest, run = applied
    settings.save(con, {"retire_cooloff_days": "0"})
    res = retire.retire(con, run)
    assert (res["albums"], res["files"]) == (1, 4)
    batch = con.execute("SELECT * FROM retire_batch").fetchone()
    return lib, dest, run, batch, Path(batch["folder"])


def files_under(folder):
    return {str(p.relative_to(folder)): sha(p) for p in Path(folder).rglob("*") if p.is_file()}


# ---- archive ------------------------------------------------------------------------------------------------
def test_archive_copies_every_parked_file_verified_and_leaves_the_parked_originals(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    before = files_under(folder)
    res = retire.archive(con, batch["id"], str(tmp_path / "backup"))
    assert (res["status"], res["verified"], res["files"], res["planned"]) == ("complete", True, 6, 6), res  # 4 + README + manifest
    arch = Path(res["dest"])
    assert arch.parent.name == "lib-originals" and arch.name == batch["key"]
    assert files_under(arch) == before                                   # every byte, same relative paths
    assert files_under(folder) == before                                 # the parked originals stay
    st = retire.archive_state(con, con.execute("SELECT * FROM retire_batch").fetchone())
    assert st["verified"] and st["files"] == 6 and st["dest"] == str(arch)
    assert retire.batches(con, [run])[0]["archive"]["verified"] is True


def test_archive_refuses_a_place_inside_the_collection_even_through_a_link(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    with pytest.raises(retire.RetireError, match="overlaps"):
        retire.archive(con, batch["id"], str(lib / "backup"))
    os.symlink(lib, tmp_path / "lnk")
    pre = retire.archive_preview(con, batch["id"], str(tmp_path / "lnk"))
    assert "overlaps" in pre["error"]
    with pytest.raises(retire.RetireError):
        retire.archive(con, batch["id"], "relative/path")
    assert not (lib / "backup").exists()


def test_archive_needs_enough_free_space_and_says_when_it_is_the_same_drive(parked, con, tmp_path, monkeypatch):
    lib, dest, run, batch, folder = parked
    pre = retire.archive_preview(con, batch["id"], str(tmp_path / "backup"))
    assert pre["error"] is None and pre["same_drive"] is True and pre["files"] == 6 and pre["bytes"] > 0
    monkeypatch.setattr(retire.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    pre = retire.archive_preview(con, batch["id"], str(tmp_path / "backup"))
    assert "not enough free space" in pre["error"]
    with pytest.raises(retire.RetireError, match="not enough free space"):
        retire.archive(con, batch["id"], str(tmp_path / "backup"))


def test_archiving_again_recognises_what_is_already_there(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    first = retire.archive(con, batch["id"], str(tmp_path / "backup"))
    again = retire.archive(con, batch["id"])                             # same place as last time
    assert again["verified"] and again["files"] == 6 and again["dest"] == first["dest"]
    assert len(list(Path(first["dest"]).rglob("*.mp3"))) == 2


def test_a_stopped_archive_is_continued_not_restarted(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    stop = threading.Event()
    stop.set()
    res = retire.archive(con, batch["id"], str(tmp_path / "backup"), stop=stop)
    assert res["status"] == "stopped" and res["verified"] is False
    assert retire.archive_state(con, con.execute("SELECT * FROM retire_batch").fetchone())["verified"] is False
    done = retire.archive(con, batch["id"])                              # no target needed: it continues
    assert done["status"] == "complete" and done["verified"] and done["files"] == 6


def test_an_archive_is_not_called_verified_when_a_file_changed_while_copying(parked, con, tmp_path, monkeypatch):
    lib, dest, run, batch, folder = parked
    real = retire.protocol.run_all

    def tamper_then_run(j, **kw):
        victim = next(p for p in folder.rglob("*.mp3"))
        victim.write_bytes(victim.read_bytes() + b"x")                   # changes size: the copy would not be the file
        return real(j, **kw)
    monkeypatch.setattr(retire.protocol, "run_all", tamper_then_run)
    res = retire.archive(con, batch["id"], str(tmp_path / "backup"))
    assert res["verified"] is False and res["problems"]
    assert retire.archive_state(con, con.execute("SELECT * FROM retire_batch").fetchone())["verified"] is False


def test_the_archive_state_notices_a_file_that_went_missing_later(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    res = retire.archive(con, batch["id"], str(tmp_path / "backup"))
    next(Path(res["dest"]).rglob("*.mp3")).unlink()
    st = retire.archive_state(con, con.execute("SELECT * FROM retire_batch").fetchone())
    assert st["verified"] is False and "missing" in st["why"]


# ---- delete: the confirmation ----------------------------------------------------------------------------------
def test_delete_needs_the_typed_word(parked, con):
    lib, dest, run, batch, folder = parked
    for word in ("", "delete", "Delete", "DELETE ", "yes"):
        with pytest.raises(retire.RetireError, match="DELETE"):
            retire.delete(con, batch["id"], word, {"no_backup": True, "checked": True})
    assert len(files_under(folder)) == 6


def test_delete_without_a_verified_archive_needs_the_no_backup_tick(parked, con):
    lib, dest, run, batch, folder = parked
    with pytest.raises(retire.RetireError, match="no verified archive"):
        retire.delete(con, batch["id"], "DELETE", {})
    assert len(files_under(folder)) == 6
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["deleted"] == 4


def test_delete_of_a_young_batch_needs_the_played_them_tick(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    settings.save(con, {"retire_cooloff_days": "7"})
    retire.archive(con, batch["id"], str(tmp_path / "backup"))
    req = retire.delete_requirements(con, con.execute("SELECT * FROM retire_batch").fetchone())
    assert req["young"] and req["has_archive"] and not req["needs_no_backup_ack"]
    with pytest.raises(retire.RetireError, match="played the organized copies"):
        retire.delete(con, batch["id"], "DELETE", {})
    assert retire.delete(con, batch["id"], "DELETE", {"checked": True})["deleted"] == 4


def test_the_cooling_off_setting_is_validated(con):
    with pytest.raises(settings.SettingsError):
        settings.save(con, {"retire_cooloff_days": "5"})
    assert settings.get(con, "retire_cooloff_days") == "7"


# ---- delete: what it does ---------------------------------------------------------------------------------------
def test_delete_frees_the_parked_files_and_keeps_everything_else(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    copies = snapshot(dest)
    archive = retire.archive(con, batch["id"], str(tmp_path / "backup"))
    out = retire.delete(con, batch["id"], "DELETE", {})
    assert (out["status"], out["deleted"], out["kept"]) == ("complete", 4, [])
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [retire.MANIFEST, retire.README]
    assert snapshot(dest) == copies                                      # the organized copies untouched
    assert len(files_under(Path(archive["dest"]))) == 6                  # the archive untouched
    b = retire.batches(con, [run])[0]
    assert (b["moved"], b["deleted"]) == (0, 4) and b["delete"]["deleted"] == 4 and b["delete"]["bytes"] > 0
    kept = Path(con.execute("PRAGMA database_list").fetchone()[2]).parent / "deleted" / f"{batch['key']}.MANIFEST.csv"
    assert kept.is_file() and len(list(csv.DictReader(open(kept, encoding="utf-8-sig")))) == 4   # the record outlives the folder
    again = retire.delete(con, batch["id"], "DELETE", {})                # nothing left to do
    assert again["deleted"] == 0
    assert retire.restore(con, batch["id"])["restored"] == 0             # nothing to restore either


def test_delete_skips_an_album_whose_organized_copy_no_longer_verifies(parked, con):
    lib, dest, run, batch, folder = parked
    tone(next(dest.rglob("01 - *.mp3")), ["-c:a", "libmp3lame"], secs=2, freq=1500)     # the copy's audio changed
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["deleted"] == 0 and "audio of the organized copy changed" in out["kept"][0]["reason"]
    assert len(files_under(folder)) == 6


def test_delete_skips_an_album_when_a_parked_file_is_not_the_one_that_was_set_aside(parked, con):
    lib, dest, run, batch, folder = parked
    victim = next(folder.rglob("01 - *.mp3"))
    st = victim.stat()
    data = bytearray(victim.read_bytes())
    data[-3000:] = bytes(b ^ 0xFF for b in data[-3000:])
    victim.write_bytes(bytes(data))
    os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))                # same size and time, other content
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["deleted"] == 0 and "content changed" in out["kept"][0]["reason"]
    assert victim.exists()


def test_a_file_that_changes_after_the_checks_is_left_alone(parked, con):
    lib, dest, run, batch, folder = parked
    j = retire.protocol.Journal(Path(batch["journal"]))
    f = next(f for info in retire._parked_albums(j).values() for f in info["files"] if f["relpath"].endswith("cover.jpg"))
    parked_path = folder / f["relpath"]
    parked_path.write_bytes(b"edited just now")
    row = dict(file_id=f["file_id"], unit=1, album="x", relpath=f["relpath"], size=f["size"], mtime_ns=f["mtime_ns"],
               content_id=f["content_id"], kind=f["kind"])
    why = retire._delete_one(con, batch, j, batch["root_real"], batch["folder"], f"f{f['file_id']}", str(parked_path), row)
    assert "changed" in why and parked_path.read_bytes() == b"edited just now"


def test_delete_never_leaves_the_batch_folder(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    outside = tmp_path / "precious.bin"
    outside.write_bytes(b"not part of the batch")
    j = retire.protocol.Journal(Path(batch["journal"]))
    row = dict(file_id=1, unit=1, album="x", relpath="../../../precious.bin", size=21, mtime_ns=outside.stat().st_mtime_ns,
               content_id="", kind="audio")
    why = retire._delete_one(con, batch, j, batch["root_real"], batch["folder"], "f1", str(folder / "../../../precious.bin"), row)
    assert why and "outside" in why and outside.read_bytes() == b"not part of the batch"


def test_delete_does_not_follow_a_link_in_place_of_a_parked_file(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    precious = tmp_path / "precious.mp3"
    victim = next(folder.rglob("01 - *.mp3"))
    shutil.copy2(victim, precious)
    victim.unlink()
    os.symlink(precious, victim)                                         # a link where a parked file was
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["deleted"] == 0 and precious.exists() and os.path.islink(victim)


def test_delete_refuses_through_a_replaced_batch_folder_or_collection(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    outside = tmp_path / "outside"
    shutil.move(str(folder), str(outside / "b" if False else outside))
    os.symlink(outside, folder)
    kept = files_under(outside)
    with pytest.raises(retire.RetireError, match="link"):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert files_under(outside) == kept
    os.unlink(folder)
    shutil.move(str(outside), str(folder))
    moved_lib = tmp_path / "moved_lib"
    shutil.move(str(lib), str(moved_lib))
    os.symlink(moved_lib, lib)
    with pytest.raises(retire.RetireError, match="collection folder"):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert len([p for p in moved_lib.rglob("*") if p.is_file() and retire.ORIGINALS in p.parts]) == 6


def test_delete_refuses_when_the_run_was_undone(parked, con):
    lib, dest, run, batch, folder = parked
    runner.undo(con, run)                                                # the organized copies are gone
    with pytest.raises(retire.RetireError, match="undone"):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    with pytest.raises(retire.RetireError, match="undone"):
        retire.delete_preview(con, batch["id"])
    assert len(files_under(folder)) == 6


def test_delete_without_the_opt_in_is_refused(parked, con):
    lib, dest, run, batch, folder = parked
    settings.set_retire_allowed(con, 1, False)
    with pytest.raises(retire.RetireError, match="not allowed"):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})


# ---- delete: crashes and stopping ------------------------------------------------------------------------------
def test_a_crash_between_two_deletes_is_finished_by_running_it_again(parked, con, monkeypatch):
    lib, dest, run, batch, folder = parked
    real, calls = os.unlink, []

    def flaky(p, *a, **k):
        if str(folder) in str(p) and not str(p).endswith(retire.README):
            calls.append(p)
            if len(calls) == 2:
                raise Crash()
        return real(p, *a, **k)
    monkeypatch.setattr(os, "unlink", flaky)
    with pytest.raises(Crash):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    monkeypatch.setattr(os, "unlink", real)
    assert len([p for p in folder.rglob("*") if p.is_file()]) == 5       # one went, the rest are intact
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["status"] == "complete" and retire.batches(con, [run])[0]["deleted"] == 4
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [retire.MANIFEST, retire.README]


def test_a_crash_after_the_unlink_but_before_its_record_is_settled(parked, con, monkeypatch):
    lib, dest, run, batch, folder = parked
    real, n = retire._record, []

    def crash_on_second_deleted(con_, batch_, j, op, row, state, **extra):
        if state == "deleted":
            n.append(op)
            if len(n) == 2:
                raise Crash()
        return real(con_, batch_, j, op, row, state, **extra)
    monkeypatch.setattr(retire, "_record", crash_on_second_deleted)
    with pytest.raises(Crash):
        retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    monkeypatch.setattr(retire, "_record", real)
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out["status"] == "complete"
    b = retire.batches(con, [run])[0]
    assert (b["moved"], b["deleted"]) == (0, 4)
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [retire.MANIFEST, retire.README]


def test_stopping_a_delete_between_files_then_running_it_again(parked, con):
    lib, dest, run, batch, folder = parked
    stop = threading.Event()

    def progress(done, total):
        if done == 1:
            stop.set()
    out = retire.delete(con, batch["id"], "DELETE", {"no_backup": True}, progress=progress, stop=stop)
    assert out["status"] == "stopped" and out["deleted"] == 1
    out2 = retire.delete(con, batch["id"], "DELETE", {"no_backup": True})
    assert out2["status"] == "complete" and out2["deleted"] == 3
    assert retire.batches(con, [run])[0]["delete"]["deleted"] == 4


# ---- delete: what the dialog tells the user ------------------------------------------------------------------------
def test_the_preview_repeats_the_checks_and_says_what_is_lost(parked, con, tmp_path):
    lib, dest, run, batch, folder = parked
    pre = retire.delete_preview(con, batch["id"])
    assert (pre["albums_ok"], pre["files_ok"], pre["albums_kept"]) == (1, 4, 0) and pre["bytes_ok"] > 0
    assert pre["lost"]["tags_files"] == 2                                # the two tracks' original (garbled) tags
    assert pre["needs_no_backup_ack"] is True and pre["has_archive"] is False
    retire.archive(con, batch["id"], str(tmp_path / "backup"))
    pre = retire.delete_preview(con, batch["id"])
    assert pre["has_archive"] is True and pre["needs_no_backup_ack"] is False
    assert len(files_under(folder)) == 6                                 # a preview deletes nothing


def test_the_preview_lists_a_cue_sheet_that_will_be_lost_with_its_album(tmp_path, con):
    lib = tmp_path / "lib"
    _cue_album(lib, "A")
    scan(con, lib)
    _accept_all_own_info(con)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    settings.set_retire_allowed(con, 1, True)
    settings.save(con, {"retire_cooloff_days": "0"})
    out = retire.retire(con, res["run"])
    pre = retire.delete_preview(con, out["batch"])
    assert pre["lost"]["rewritten_cues"] == 1 and pre["albums_ok"] == 1
