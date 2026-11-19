"""Thin slice end to end: accept -> plan -> apply (copy, tag the copy, verify, publish) -> undo."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from mp3librarytool.apply import naming, plan, runner
from mp3librarytool.apply import protocol
from mp3librarytool.hashing import audio_hash
from mp3librarytool.recognize import units
from mp3librarytool.scanner import scan

from .conftest import needs_ffmpeg, tag_id3, tone

pytestmark = needs_ffmpeg


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def accepted(tmp_path, con):
    """An album folder with garbled tags + an .lrc + a cover, recognized and accepted."""
    lib = tmp_path / "lib"
    a = lib / "Ò¹º½"
    for i in range(2):
        p = tone(a / f"0{i + 1} - x.mp3", ["-c:a", "libmp3lame"], secs=2, freq=400 + 50 * i)
        tag_id3(p, "夜航".encode("gbk").decode("latin-1"), artist="周海晴".encode("gbk").decode("latin-1"),
                album="Ò¹º½", encoding=0)
    (a / "01 - x.lrc").write_text("[00:00.00]歌词\n", encoding="utf-8")
    (a / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done' WHERE id=?", (uid,))
    cid = con.execute("INSERT INTO candidate(unit_id, rank, source, album_id, album, artist, year, mediums, "
                      "distance, fingerprint_agrees, n_mapped) VALUES(?,0,'MusicBrainz','alb-1','夜航','周海晴',"
                      "2004,1,0.06,2,2)", (uid,)).lastrowid
    for fid, rel in con.execute("SELECT id, relpath FROM file WHERE kind='audio' ORDER BY relpath").fetchall():
        n = int(rel.split("/")[-1][:2])
        con.execute("INSERT INTO cand_track VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, fid, f"rec-{n}", f"rt-{n}", n, 1, n, ["序曲", "夜航"][n - 1], "周海晴", 2.0, 0.0))
    con.execute("INSERT INTO decision VALUES(?, 'accept', ?, 0)", (uid, cid))
    con.commit()
    return lib, tmp_path / "Organized"


def snapshot(lib):
    return {str(p): sha(p) for p in Path(lib).rglob("*") if p.is_file()}


def test_plan_lists_copies_tags_and_side_files(accepted, con):
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    p = plan.load(con, pid)
    dests = sorted(i["dest_rel"] for i in p["items"])
    assert dests == ["周海晴/夜航 (2004)/01 - 序曲.lrc", "周海晴/夜航 (2004)/01 - 序曲.mp3",
                     "周海晴/夜航 (2004)/02 - 夜航.mp3", "周海晴/夜航 (2004)/cover.jpg"]
    assert p["summary"]["ok"] == 4 and not dest.exists()      # planning writes nothing


def test_apply_writes_tags_to_copies_only_and_undo(accepted, con, tmp_path):
    lib, dest = accepted
    before = snapshot(lib)
    pid = plan.build(con, str(dest), 1)
    res = runner.apply(con, pid, journal_root=tmp_path / "journal")
    assert res["states"] == {"committed": 4}, res
    assert snapshot(lib) == before                            # sources untouched
    from mutagen.easyid3 import EasyID3
    t = EasyID3(dest / "周海晴/夜航 (2004)/02 - 夜航.mp3")
    assert t["title"] == ["夜航"] and t["artist"] == ["周海晴"] and t["album"] == ["夜航"]
    assert t["musicbrainz_trackid"] == ["rec-2"] and t["date"] == ["2004"]
    src = lib / "Ò¹º½/02 - x.mp3"
    assert audio_hash(str(dest / "周海晴/夜航 (2004)/02 - 夜航.mp3"), ".mp3")[0] == audio_hash(str(src), ".mp3")[0]
    assert not list(dest.rglob(".*mlt-*"))                    # no temp files left
    u = runner.undo(con, res["run"])
    assert u["actions"] == {"removed": 4}
    assert not any(p.is_file() for p in dest.rglob("*"))


def test_undo_keeps_a_copy_you_edited(accepted, con, tmp_path):
    lib, dest = accepted
    res = runner.apply(con, plan.build(con, str(dest), 1), journal_root=tmp_path / "j")
    edited = dest / "周海晴/夜航 (2004)/01 - 序曲.lrc"
    edited.write_text("[00:00.00]my own lyrics\n", encoding="utf-8")
    u = runner.undo(con, res["run"])
    assert u["actions"].get("kept: changed since it was created") == 1 and edited.exists()


def test_existing_destination_is_a_conflict_never_overwritten(accepted, con, tmp_path):
    lib, dest = accepted
    target = dest / "周海晴/夜航 (2004)/02 - 夜航.mp3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone else's file")
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert {i["dest_rel"]: i["status"] for i in p["items"]}["周海晴/夜航 (2004)/02 - 夜航.mp3"] == "conflict_existing"
    runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert target.read_bytes() == b"someone else's file"


def test_source_changed_after_plan_is_not_copied(accepted, con, tmp_path):
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    src = lib / "Ò¹º½/01 - x.mp3"
    os.utime(src, ns=(0, src.stat().st_mtime_ns + 10_000_000))
    res = runner.apply(con, pid, journal_root=tmp_path / "j")
    assert res["states"].get("aborted_source_changed") == 1
    assert not (dest / "周海晴/夜航 (2004)/01 - 序曲.mp3").exists()


@pytest.mark.parametrize("point", ["mid_copy", "after_copy", "after_tag", "after_publish"])
def test_crash_then_resume_completes_without_duplicates(accepted, con, tmp_path, point):
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    p = plan.load(con, pid)
    method = protocol.choose_method(dest)
    j = protocol.Journal(tmp_path / f"j-{point}.jsonl")
    for it in p["items"]:
        protocol.plan_op(j, "r1", f"i{it['seq']}", Path(it["src"]), dest / it["dest_rel"], it["kind"],
                         it["codec"], it["tags"], method, it["src_size"], it["src_mtime_ns"])
    crash_op = next(f"i{it['seq']}" for it in p["items"] if it["kind"] == "audio")
    pid_ = os.fork()
    if pid_ == 0:
        try:
            protocol.run_all(j, crash_at=point, crash_op=crash_op)
        finally:
            os._exit(0)
    _, status = os.waitpid(pid_, 0)
    assert os.WEXITSTATUS(status) == 137
    protocol.recover(j)
    protocol.run_all(j)
    states = {o["history"][-1] for o in j.ops().values()}
    assert states == {"committed"}
    names = [x.name for x in dest.rglob("*") if x.is_file()]
    assert len(names) == len(set(names)) == 4 and not [n for n in names if ".mlt-" in n]


@pytest.mark.parametrize("raw,safe", [("AC/DC: Live?", "AC_DC_ Live_"), ("CON", "_CON"), ("trailing. ", "trailing"),
                                      ("周杰倫 *新歌*", "周杰倫 _新歌_")])
def test_names_are_windows_safe(raw, safe):
    assert naming.safe_component(raw) == safe


def test_extension_follows_the_real_codec():
    assert naming.track_name("x", 1, 1, 1, "ape", ".mp3").endswith(".ape")
    assert naming.track_name("x", 3, 2, 2, "flac", ".flac") == "2-03 - x.flac"


@pytest.mark.parametrize("where", ["inside", "same", "parent"])
def test_destination_may_not_overlap_the_collection(accepted, con, where):
    lib, _ = accepted
    dest = {"inside": lib / "Organized", "same": lib, "parent": lib.parent}[where]
    with pytest.raises(plan.PlanError):
        plan.build(con, str(dest), 1)
