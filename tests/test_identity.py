"""ADR 0002: identity follows the content, not the path."""
import json
import os
import shutil

import pytest

from music_steward import identity
from music_steward.apply import plan
from music_steward.recognize import units
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tone
from .test_apply import accepted  # noqa: F401  (fixture: one accepted album, two MP3s + lrc + cover)

pytestmark = needs_ffmpeg


def test_identity_ignores_tags_even_when_the_audio_moves(tmp_path):
    from mutagen.flac import FLAC
    from mutagen.id3 import APIC, ID3, TIT2
    from mutagen.apev2 import APEv2
    f = tone(tmp_path / "a.flac", ["-c:a", "flac"], secs=4)
    m = tone(tmp_path / "a.mp3", ["-c:a", "libmp3lame"], secs=4)
    before = {p: identity.audio_identity(str(p)) for p in (f, m)}
    x = FLAC(f)
    x["COMMENT"] = "z" * 200_000                  # beyond the padding: the audio frames move
    x.save()
    y = ID3()
    y.add(TIT2(encoding=3, text="新标题"))
    y.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="c", data=b"\xff" * 300_000))
    y.save(m)
    ape = APEv2()
    ape["Title"] = "尾部标签"
    ape.save(m)                                   # an APEv2 tag at the end as well
    assert {p: identity.audio_identity(str(p)) for p in (f, m)} == before
    g = tone(tmp_path / "b.flac", ["-c:a", "flac"], secs=4, freq=900)
    assert identity.audio_identity(str(g)) != before[f]
    assert before[f].startswith("e1:")


def test_every_file_gets_a_content_id_and_old_rows_are_filled_in(accepted, con):  # noqa: F811
    lib, _ = accepted
    rows = con.execute("SELECT kind, content_id FROM file").fetchall()
    assert rows and all(r["content_id"] for r in rows)                  # audio e1:, lrc/cover s1:
    assert {r["content_id"][:3] for r in rows} == {"e1:", "s1:"}
    con.execute("UPDATE file SET content_id=NULL")                        # a database from before 0.4
    con.commit()
    r = scan(con, lib)
    assert r.counts["n_unchanged"] == len(rows) and r.counts["n_changed"] == 0
    assert all(x["content_id"] for x in con.execute("SELECT content_id FROM file"))


def _unit(con):
    return con.execute("SELECT u.*, d.action, d.stale_reason FROM unit u LEFT JOIN decision d ON d.unit_id=u.id").fetchone()


def test_renamed_folder_keeps_its_album_and_decision(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    before = _unit(con)
    ids_before = {r["relpath"].rsplit("/", 1)[1]: r["id"] for r in con.execute("SELECT id, relpath FROM file")}
    os.rename(lib / "Ò¹º½", lib / "周海晴 - 夜航")
    r = scan(con, lib)
    assert r.counts["n_moved"] == 4 and r.counts["n_new"] == 0 and r.counts["n_removed"] == 0
    ids_after = {r["relpath"].rsplit("/", 1)[1]: r["id"] for r in con.execute("SELECT id, relpath FROM file")}
    assert ids_after == ids_before                                       # same rows, new paths
    res = units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    assert res["moved"] == 1 and res["removed_stale"] == 0
    after = _unit(con)
    assert (after["id"], after["action"], after["status"]) == (before["id"], "accept", "done")
    assert after["folder"] == "周海晴 - 夜航"
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert {i["status"] for i in p["items"]} == {"ok"} and all("周海晴 - 夜航" in i["src"] for i in p["items"])


def test_a_file_moved_to_a_folder_scanned_on_its_own_keeps_its_row(accepted, con):  # noqa: F811
    lib, _ = accepted
    fid = con.execute("SELECT id FROM file WHERE relpath LIKE '%01 - x.mp3'").fetchone()[0]
    (lib / "Elsewhere").mkdir()
    os.rename(lib / "Ò¹º½/01 - x.mp3", lib / "Elsewhere/01 - x.mp3")
    r = scan(con, str(lib / "Elsewhere"))                     # only the new folder is scanned
    assert r.counts["n_moved"] == 1
    row = con.execute("SELECT relpath, removed_at FROM file WHERE id=?", (fid,)).fetchone()
    assert (row["relpath"], row["removed_at"]) == ("Elsewhere/01 - x.mp3", None)
    assert scan(con, lib).counts["n_removed"] == 0           # the old path is not "removed" later


def test_a_copy_next_to_its_original_is_a_new_file(accepted, con):  # noqa: F811
    lib, _ = accepted
    (lib / "Copy").mkdir()
    shutil.copy2(lib / "Ò¹º½/01 - x.mp3", lib / "Copy/01 - x.mp3")
    r = scan(con, lib)
    assert (r.counts["n_new"], r.counts["n_moved"]) == (1, 0)
    a, b = con.execute("SELECT content_id FROM file WHERE relpath LIKE '%01 - x.mp3'").fetchall()
    assert a[0] == b[0]                                       # same content, two files


def test_changed_audio_flags_the_decision_and_leaves_the_album_out_of_plans(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    src = lib / "Ò¹º½/02 - x.mp3"
    tone(src, ["-c:a", "libmp3lame"], secs=3, freq=1200)    # a different recording, same name
    r = scan(con, lib)
    assert r.counts["n_changed"] == 1
    u = _unit(con)
    assert u["action"] == "accept" and "changed since you decided" in u["stale_reason"]
    assert u["status"] == "queued"                            # recognized again on the next run
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert p["items"] == [] and [x["label"] for x in p["summary"]["left_out"]] == [u["label"]]
    # deciding again (the app writes a new decision row) clears the flag
    con.execute("INSERT OR REPLACE INTO decision(unit_id, action, candidate_id, decided_at) "
                "SELECT unit_id, 'accept', candidate_id, 1 FROM decision")
    con.commit()
    assert _unit(con)["stale_reason"] is None


def test_a_tag_edit_of_a_source_does_not_flag_the_decision(accepted, con):  # noqa: F811
    from mutagen.id3 import ID3, TIT2
    lib, _ = accepted
    t = ID3(lib / "Ò¹º½/01 - x.mp3")
    t.add(TIT2(encoding=3, text="另一个标题" * 40))
    t.save()
    r = scan(con, lib)
    assert r.counts["n_changed"] == 1
    assert _unit(con)["stale_reason"] is None and _unit(con)["status"] == "done"


def test_a_changed_cue_sheet_flags_its_album(tmp_path, con):
    from .test_cue import CUE
    lib = tmp_path / "lib"
    a = lib / "HIFI"
    tone(a / "image.flac", ["-c:a", "flac"], secs=6)
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done'")
    con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'accept', 0)", (uid,))
    con.commit()
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").replace("怀念战友", "别的").encode("gbk"))
    os.utime(a / "image.cue", ns=(0, os.stat(a / "image.cue").st_mtime_ns + 10**9))
    scan(con, lib)
    assert "changed" in con.execute("SELECT stale_reason FROM decision").fetchone()[0]
    assert json.loads(con.execute("SELECT file_ids FROM unit").fetchone()[0])        # the album is still there


def test_an_interrupted_scan_never_saves_changed_audio_without_the_flag(accepted, con, monkeypatch):  # noqa: F811
    """Codex review on #6: the new content_id was committed every N files, the flag only at the
    end -- an interrupt in between left the decision unflagged for good."""
    from music_steward import scanner
    lib, _ = accepted
    q = "SELECT content_id, size FROM file WHERE relpath LIKE '%02 - x.mp3'"
    old = tuple(con.execute(q).fetchone())
    tone(lib / "Ò¹º½/02 - x.mp3", ["-c:a", "libmp3lame"], secs=3, freq=1200)
    monkeypatch.setattr(scanner, "COMMIT_EVERY", 1)

    def stop(done, total):
        raise KeyboardInterrupt                        # right after a commit
    with pytest.raises(KeyboardInterrupt):
        scan(con, lib, progress=stop)
    con.rollback()                                     # anything not committed is lost
    saved = tuple(con.execute(q).fetchone())
    # either nothing about the new audio was saved, or it was saved together with the flag
    assert saved == old or _unit(con)["stale_reason"]
    scan(con, lib)
    assert _unit(con)["stale_reason"]


def test_a_replaced_cue_sheet_keeps_the_decision_but_asks_for_review(tmp_path, con):
    """Codex review on #6: the same image with a new cue sheet (another name, another layout) moved
    the old decision over silently."""
    from .test_cue import CUE
    lib = tmp_path / "lib"
    a = lib / "HIFI"
    tone(a / "image.flac", ["-c:a", "flac"], secs=6)
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"], scope="")
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done'")
    con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'accept', 0)", (uid,))
    con.commit()
    (a / "image.cue").unlink()
    two = CUE.replace("old image name.wav", "image.wav").replace("INDEX 01 00:04:00", "INDEX 01 00:03:00")
    (a / "new name.cue").write_bytes(two.encode("gbk"))
    scan(con, lib)
    res = units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"], scope="")
    assert res["moved"] == 1
    u = _unit(con)
    assert u["id"] == uid and u["action"] == "accept" and "cue sheet was replaced" in u["stale_reason"]
    assert u["status"] == "queued"


@pytest.mark.parametrize("copy_first", [True, False])
def test_a_restored_file_and_a_new_copy_of_it_are_two_files(accepted, con, monkeypatch, copy_first):  # noqa: F811
    """Codex review on #6: a copy could take over the row of a removed file restored in the same
    scan (removed_at still set), leaving one row for two files."""
    from music_steward import scanner
    lib, _ = accepted
    src = lib / "Ò¹º½/01 - x.mp3"
    keep = lib.parent / "kept.mp3"
    shutil.move(src, keep)
    scan(con, lib)                                       # the file is now marked removed
    assert con.execute("SELECT removed_at FROM file WHERE relpath LIKE '%01 - x.mp3'").fetchone()[0]
    shutil.copy2(keep, src)                              # restored to its old path ...
    (lib / "Copy").mkdir()
    shutil.copy2(keep, lib / "Copy/01 - x.mp3")          # ... and a copy of it elsewhere
    real = scanner.as_completed                          # make the order deterministic either way

    def ordered(futs):
        done = list(real(futs))
        return sorted(done, key=lambda fu: ("Copy" in futs[fu].rel) != copy_first) if isinstance(futs, dict) and \
            all(hasattr(v, "rel") for v in futs.values()) else done
    monkeypatch.setattr(scanner, "as_completed", ordered)
    r = scan(con, lib)
    rows = con.execute("SELECT relpath, fs_relpath, removed_at FROM file WHERE relpath LIKE '%01 - x.mp3'").fetchall()
    assert sorted((x["relpath"], x["fs_relpath"], x["removed_at"]) for x in rows) == [
        ("Copy/01 - x.mp3", "Copy/01 - x.mp3", None), ("Ò¹º½/01 - x.mp3", "Ò¹º½/01 - x.mp3", None)]
    assert r.counts["n_moved"] == 0


def test_a_file_moved_and_retagged_loses_its_stale_hashes_but_a_rename_keeps_them(accepted, con):  # noqa: F811
    """Codex review on #6: the exact-duplicate audit used the old whole-file hash of a moved and
    retagged file."""
    from mutagen.id3 import ID3, TIT2
    from music_steward.hashpass import run as hash_run
    lib, _ = accepted
    hash_run(con, 1)
    q = "SELECT file_sha256, relpath FROM file WHERE relpath LIKE '%0{n} - x.mp3'"
    assert con.execute(q.format(n=1)).fetchone()[0] and con.execute(q.format(n=2)).fetchone()[0]
    (lib / "Moved").mkdir()
    os.rename(lib / "Ò¹º½/01 - x.mp3", lib / "Moved/01 - x.mp3")          # moved only
    os.rename(lib / "Ò¹º½/02 - x.mp3", lib / "Moved/02 - x.mp3")          # moved and retagged
    t = ID3(lib / "Moved/02 - x.mp3")
    t.add(TIT2(encoding=3, text="新的标题"))
    t.save()
    assert scan(con, lib).counts["n_moved"] == 2
    one, two = con.execute(q.format(n=1)).fetchone(), con.execute(q.format(n=2)).fetchone()
    assert one["relpath"].startswith("Moved/") and one["file_sha256"]          # same bytes: hash kept
    assert two["relpath"].startswith("Moved/") and two["file_sha256"] is None  # new bytes: hash cleared


def _albums(con):
    return {r["folder"]: (r["id"], r["action"]) for r in con.execute(
        "SELECT u.id, u.folder, d.action FROM unit u LEFT JOIN decision d ON d.unit_id=u.id")}


def _two_albums(tmp_path, con):
    lib = tmp_path / "lib"
    for name, freq in (("A", 300), ("B", 700)):
        for i in range(2):
            tone(lib / name / f"{name}{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=freq + 50 * i)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    for (uid,) in con.execute("SELECT id FROM unit").fetchall():
        con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'accept', 0)", (uid,))
    con.commit()
    return lib


def test_a_new_album_in_the_old_folder_cannot_take_the_moved_albums_decision(tmp_path, con):
    """Codex review on #6: album moved A -> C, a different album added to A: the moved album lost its
    decision to the newcomer."""
    lib = _two_albums(tmp_path, con)
    before = _albums(con)
    os.rename(lib / "A", lib / "C")
    for i in range(2):
        tone(lib / "A" / f"new{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=1500 + 50 * i)
    scan(con, lib)
    res = units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    after = _albums(con)
    assert after["C"] == before["A"]                    # same unit, decision kept
    assert after["B"] == before["B"]
    assert after["A"][1] is None and after["A"][0] not in (before["A"][0], before["B"][0])
    assert res["moved"] == 1 and res["removed_stale"] == 0


def test_two_swapped_folders_keep_their_own_decisions(tmp_path, con):
    lib = _two_albums(tmp_path, con)
    con.execute("UPDATE decision SET action='skip' WHERE unit_id=(SELECT id FROM unit WHERE folder='B')")
    con.commit()
    before = _albums(con)
    os.rename(lib / "A", lib / "tmp")
    os.rename(lib / "B", lib / "A")
    os.rename(lib / "tmp", lib / "B")
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    after = _albums(con)
    assert after["A"] == before["B"] and after["B"] == before["A"]


def test_swapped_folders_with_the_same_file_names_keep_their_own_decisions(tmp_path, con):
    """Codex review on #6: A/01.mp3 and B/01.mp3 swapped -- every path still exists, so the rows were
    overwritten in place and the accept and the skip ended up on the other album's recordings."""
    lib = tmp_path / "lib"
    for name, freq in (("A", 300), ("B", 700)):
        for i in (1, 2):
            tone(lib / name / f"0{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=freq + 50 * i)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    for (uid, folder) in con.execute("SELECT id, folder FROM unit").fetchall():
        con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, ?, 0)",
                    (uid, "accept" if folder == "A" else "skip"))
    con.commit()
    before = _albums(con)
    file_of = {r["content_id"]: r["id"] for r in con.execute("SELECT id, content_id FROM file")}
    os.rename(lib / "A", lib / "tmp")
    os.rename(lib / "B", lib / "A")
    os.rename(lib / "tmp", lib / "B")
    r = scan(con, lib)
    assert (r.counts["n_moved"], r.counts["n_changed"], r.counts["n_new"], r.counts["n_removed"]) == (4, 0, 0, 0)
    assert {r["content_id"]: r["id"] for r in con.execute("SELECT id, content_id FROM file")} == file_of
    units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    after = _albums(con)
    assert after["A"] == before["B"] and after["B"] == before["A"]      # skip and accept followed the audio
    assert con.execute("SELECT count(*) FROM decision WHERE stale_reason IS NOT NULL").fetchone()[0] == 0


def test_a_file_replaced_by_one_moved_in_from_elsewhere(tmp_path, con):
    """X/01.mp3 moved over Y/01.mp3 (whose own audio is now nowhere): the row follows X's audio, Y's
    old row is removed and its album flagged."""
    lib = tmp_path / "lib"
    tone(lib / "X/01.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300)
    tone(lib / "Y/01.mp3", ["-c:a", "libmp3lame"], secs=1, freq=900)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1), scope="")
    for (uid,) in con.execute("SELECT id FROM unit").fetchall():
        con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'accept', 0)", (uid,))
    con.commit()
    x_id = con.execute("SELECT id FROM file WHERE relpath='X/01.mp3'").fetchone()[0]
    y_id = con.execute("SELECT id FROM file WHERE relpath='Y/01.mp3'").fetchone()[0]
    os.replace(lib / "X/01.mp3", lib / "Y/01.mp3")
    r = scan(con, lib)
    assert (r.counts["n_moved"], r.counts["n_removed"]) == (1, 1)
    assert con.execute("SELECT relpath FROM file WHERE id=?", (x_id,)).fetchone()[0] == "Y/01.mp3"
    assert con.execute("SELECT removed_at FROM file WHERE id=?", (y_id,)).fetchone()[0]
    flagged = con.execute("SELECT u.folder FROM unit u JOIN decision d ON d.unit_id=u.id "
                          "WHERE d.stale_reason IS NOT NULL").fetchall()
    assert [f[0] for f in flagged] == ["Y"]
