"""Thin slice end to end: accept -> plan -> apply (copy, tag the copy, verify, publish) -> undo."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from music_steward.apply import naming, plan, runner
from music_steward.apply import protocol
from music_steward.hashing import audio_hash
from music_steward.recognize import units
from music_steward.scanner import scan

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
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at) VALUES(?, 'accept', ?, 0)", (uid, cid))
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


def _two_disc_album(tmp_path, con, same_cover: bool):
    lib = tmp_path / "lib2"
    for d in ("CD 1", "CD 2"):
        tag_id3(tone(lib / "Box" / d / "01.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 if d == "CD 1" else 600),
                f"T {d}", album="Box")
        (lib / "Box" / d / "Folder.jpg").write_bytes(b"\xff\xd8same" if same_cover else f"\xff\xd8{d}".encode())
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    cid = con.execute("INSERT INTO candidate(unit_id, rank, album_id, album, artist, year, mediums, distance) "
                      "VALUES(?,0,'b1','Box','A',2000,2,0.05)", (uid,)).lastrowid
    for fid, rel in con.execute("SELECT id, relpath FROM file WHERE kind='audio' ORDER BY relpath").fetchall():
        disc = 1 if "CD 1" in rel else 2
        con.execute("INSERT INTO cand_track VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, fid, f"r{disc}", None, disc, disc, 1, f"Song {disc}", "A", 1.0, 0.0))
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at) VALUES(?, 'accept', ?, 0)", (uid, cid))
    con.commit()
    return lib


def test_identical_disc_covers_are_copied_once(tmp_path, con):
    _two_disc_album(tmp_path, con, same_cover=True)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    imgs = [i["dest_rel"] for i in p["items"] if i["kind"] == "image"]
    assert imgs == ["A/Box (2000)/Folder.jpg"]
    assert p["summary"]["duplicate_images_skipped"] == 1 and p["summary"]["blocked"] == 0


def test_different_disc_covers_are_both_kept(tmp_path, con):
    _two_disc_album(tmp_path, con, same_cover=False)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    imgs = sorted(i["dest_rel"] for i in p["items"] if i["kind"] == "image")
    assert imgs == ["A/Box (2000)/Folder (CD 2).jpg", "A/Box (2000)/Folder.jpg"]
    assert p["summary"]["blocked"] == 0


def test_non_musicbrainz_release_never_writes_musicbrainz_ids(accepted, con):
    lib, dest = accepted
    con.execute("UPDATE candidate SET source='Deezer', album_id='302127'")
    con.commit()
    p = plan.load(con, plan.build(con, str(dest), 1))
    for it in (i for i in p["items"] if i["kind"] == "audio"):
        assert it["tags"]["mb_trackid"] is None and it["tags"]["mb_albumid"] is None
        assert it["tags"]["album"] == "夜航"


def test_sources_follow_environment(monkeypatch):
    from music_steward.recognize import beets_adapter as A
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    monkeypatch.delenv("MLT_SOURCES", raising=False)
    assert A.sources() == ["musicbrainz", "deezer"]
    monkeypatch.setenv("DISCOGS_TOKEN", "x")
    assert A.sources() == ["musicbrainz", "deezer", "discogs"]
    monkeypatch.setenv("MLT_SOURCES", "musicbrainz")
    assert A.sources() == ["musicbrainz"]


def test_tags_written_to_a_copy_whose_ape_tag_is_corrupt(tmp_path):
    """mutagen refuses some real APE tags (GBK bytes, broken item lengths); the copy gets a clean
    tag with the readable items kept (GBK converted) and the new fields set."""
    import struct
    from mutagen.apev2 import APEv2
    from music_steward.apply.tagwrite import write_tags
    from music_steward.tags import _raw_apev2

    def item(k, v):
        return struct.pack("<2I", len(v), 0) + k + b"\x00" + v
    body = item(b"Artist", "孟庭苇".encode("gbk")) + item(b"Comment", b"ripped 1995") + item(b"Genre", b"Other")
    tag = body + b"APETAGEX" + struct.pack("<4I", 2000, len(body) + 32, 3, 0) + b"\x00" * 8
    p = tmp_path / "copy.ape"
    p.write_bytes(b"MAC \x96\x0f" + b"\x00" * 100 + tag)
    try:
        APEv2(str(p))
        raise AssertionError("fixture should be unreadable for mutagen")
    except Exception as e:  # noqa: BLE001
        assert "utf-8" in str(e)
    write_tags(str(p), {"title": "真的还是假的", "album": "真的还是假的", "track": 1}, "ape")
    t = APEv2(str(p))
    assert str(t["Title"]) == "真的还是假的" and str(t["Track"]) == "1"
    assert str(t["Artist"]) == "孟庭苇" and str(t["Comment"]) == "ripped 1995" and str(t["Genre"]) == "Other"
    assert p.read_bytes().count(b"APETAGEX") == 2 and p.read_bytes().startswith(b"MAC ")   # one tag (header+footer)
    assert len(_raw_apev2(str(p))) == 6


def test_tags_written_to_a_wav_copy(tmp_path):
    """A cue album whose image is WAV (陈松伶 天涯歌女 on the NAS) failed: WAV tags are ID3 frames."""
    import mutagen
    from music_steward.apply.tagwrite import write_tags
    from .conftest import tone
    p = tone(tmp_path / "天涯歌女.wav", ["-c:a", "pcm_s16le"], secs=1)
    write_tags(str(p), {"album": "天涯歌女", "albumartist": "陈松伶", "date": 1990, "disc": "1/2"}, "pcm_s16le")
    t = mutagen.File(str(p)).tags
    assert str(t["TALB"]) == "天涯歌女" and str(t["TPE2"]) == "陈松伶" and str(t["TPOS"]) == "1/2"


def test_ape_copy_fingerprint_ignores_tags_and_catches_audio_changes(tmp_path):
    """The apply check for APE hashes the audio BYTES (between an ID3 prefix and the end tags)
    instead of decoding twice: tag writes must not change it, any audio byte must."""
    from mutagen.apev2 import APEv2
    from mutagen.id3 import ID3, TIT2
    from music_steward.apply.tagwrite import write_tags
    from music_steward.hashing import copy_audio_fingerprint
    p = tmp_path / "a.ape"
    p.write_bytes(b"MAC \x96\x0f" + bytes(range(256)) * 40)
    t = ID3()
    t.add(TIT2(encoding=3, text=["prefix"]))
    t.save(str(p))                                      # ID3 block in front, as on the NAS
    f0 = copy_audio_fingerprint(str(p), ".ape", "ape")
    write_tags(str(p), {"title": "新", "album": "专辑", "track": 3}, "ape")
    assert copy_audio_fingerprint(str(p), ".ape", "ape") == f0
    a = APEv2(str(p))
    a["Comment"] = "x" * 5000
    a.save(str(p))                                      # a much bigger tag: still the same audio
    with open(p, "ab") as fh:
        fh.write(b"TAG" + b"\x00" * 125)                # plus an ID3v1 tag at the very end
    assert copy_audio_fingerprint(str(p), ".ape", "ape") == f0
    raw = bytearray(p.read_bytes())
    i = raw.find(b"MAC ") + 100
    raw[i] ^= 1                                         # one audio bit
    p.write_bytes(bytes(raw))
    assert copy_audio_fingerprint(str(p), ".ape", "ape") != f0


def test_stop_between_files_then_resume_finishes(accepted, con, tmp_path):
    """Stop (UI) halts apply after the current file; nothing half-written; Resume completes the run."""
    import threading
    from music_steward.apply import runner
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    stop = threading.Event()
    seen = []

    def progress(i, n):
        seen.append(i)
        if i == 1:
            stop.set()
    r = runner.apply(con, pid, progress=progress, journal_root=tmp_path / "j", stop=stop)
    assert seen == [1] and r["states"].get("committed") == 1 and r["states"].get("intent", 0) >= 1
    assert not list(dest.rglob(".*mlt-*"))
    r2 = runner.resume(con, r["run"])
    assert set(r2["states"]) == {"committed"}


def test_crash_while_journalling_the_plan_then_resume_copies_every_file(accepted, con, tmp_path, monkeypatch):
    """A run interrupted after journalling only part of its plan must not finish as 'complete'
    with the rest silently missing: Resume journals the missing files and copies them."""
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    n_items = sum(1 for it in plan.load(con, pid)["items"] if it["status"] == "ok")
    assert n_items == 4
    real, calls = protocol.plan_op, []

    def plan_op_then_crash(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            os._exit(137)                       # power loss after 2 of 4 intents
        return real(*a, **k)
    child = os.fork()
    if child == 0:
        try:
            monkeypatch.setattr(protocol, "plan_op", plan_op_then_crash)
            runner.apply(con, pid, journal_root=tmp_path / "j")
        finally:
            os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 137
    run = con.execute("SELECT * FROM run").fetchone()
    j = protocol.Journal(Path(run["journal"]))
    assert len(j.ops()) == 2 and "planned" not in j.meta()
    res = runner.resume(con, run["id"])
    assert res["states"] == {"committed": 4} and res["complete_plan"] is True, res
    assert len([x for x in dest.rglob("*") if x.is_file()]) == 4


def test_a_torn_last_journal_line_does_not_swallow_the_next_record(tmp_path):
    j = protocol.Journal(tmp_path / "j.jsonl")
    j.write({"op": "a", "state": "intent"})
    with open(j.path, "a") as fh:
        fh.write('{"op": "b", "sta')              # crash mid-write: no newline
    j.write({"op": "c", "state": "intent"})
    assert set(j.ops()) == {"a", "c"}


def test_a_link_into_the_collection_made_after_planning_is_never_written_through(accepted, con, tmp_path):
    """The plan checked the destination; then the album's folder at the destination became a link
    into the collection. Apply must refuse those files, not write into the collection."""
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    before = snapshot(lib)
    (dest / "周海晴").mkdir(parents=True)
    os.symlink(lib / "Ò¹º½", dest / "周海晴/夜航 (2004)")
    res = runner.apply(con, pid, journal_root=tmp_path / "j")
    assert res["states"] == {"aborted_unsafe_destination": 4}, res
    assert all("collection" in f["reason"] for f in res["failed"])
    assert snapshot(lib) == before                     # nothing written into the collection


def test_a_link_out_of_the_destination_is_refused(accepted, con, tmp_path):
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dest.mkdir()
    os.symlink(elsewhere, dest / "周海晴")
    res = runner.apply(con, pid, journal_root=tmp_path / "j")
    assert res["states"] == {"aborted_unsafe_destination": 4}, res
    assert not list(elsewhere.rglob("*"))


def test_a_collection_folder_added_after_planning_blocks_apply_and_resume(accepted, con, tmp_path):
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    dest.mkdir()
    con.execute("INSERT INTO root(path, added_at) VALUES(?, 0)", (str(dest),))
    con.commit()
    res = runner.apply(con, pid, journal_root=tmp_path / "j")
    assert "overlaps" in res["error"] and not con.execute("SELECT 1 FROM run").fetchone()
    assert not list(dest.rglob("*"))
