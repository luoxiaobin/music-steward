"""Setting "Albums of one file with a cue sheet: one file per track": lossless, sample-exact split."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from music_steward import cue
from music_steward.apply import plan, protocol, runner, split
from music_steward.recognize import local, units
from music_steward.scanner import scan

from .conftest import needs_ffmpeg
from .test_cue import CUE

pytestmark = needs_ffmpeg


def noise(path: Path, secs: float, rate: int = 44100, fmt: str = "s16", codec: list[str] | None = None) -> Path:
    """Music-like content (not a pure tone), so a cut a sample off would change the hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=d={secs}:c=pink:r={rate}:a=0.3", "-ac", "2",
                    *(codec or ["-sample_fmt", fmt, "-c:a", "flac"]), str(path)], check=True)
    return path


def samples(path, bits=16) -> bytes:
    codec = "pcm_s16le" if bits == 16 else "pcm_s32le"
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", codec, "-f",
                           codec[4:], "-"], capture_output=True, check=True).stdout


def stream(path) -> dict:
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                                     capture_output=True, check=True).stdout)["streams"][0]


@pytest.mark.parametrize("rate,fmt,bits", [(44100, "s16", 16), (96000, "s32", 24)])
def test_tracks_joined_again_equal_the_image_sample_for_sample(tmp_path, rate, fmt, bits):
    img = noise(tmp_path / "img.flac", 12, rate, fmt)
    starts = [0, (3 * 75 + 37) / 75, (7 * 75 + 1) / 75, (10 * 75 + 74) / 75]     # cue frames, 1/75 s
    joined = b""
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else None
        out = tmp_path / f"{i}.flac"
        split.extract(str(img), str(out), s, e, rate, bits, ".flac")
        assert split.verify(str(img), str(out), s, e, rate, bits, ".flac")
        joined += samples(out, bits)
    assert joined == samples(img, bits)
    st = stream(tmp_path / "1.flac")
    assert st["sample_fmt"] == ("s16" if bits == 16 else "s32") and int(st["sample_rate"]) == rate


def test_verify_catches_a_track_that_is_off_by_a_little(tmp_path):
    img = noise(tmp_path / "img.flac", 6)
    out = tmp_path / "t.flac"
    split.extract(str(img), str(out), 2.0, 4.0, 44100, 16, ".flac")
    assert not split.verify(str(img), str(out), 2.0 + 1 / 75, 4.0, 44100, 16, ".flac")


@pytest.fixture
def split_album(tmp_path, con, monkeypatch):
    """A 6 s lossless image with a 3-track GBK cue sheet, accepted with its own info; setting on."""
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    img = noise(lib / "HIFI/image.flac", 6)
    (lib / "HIFI/image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3")
    cid = local.add(con, uid)
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    return lib, img


def test_a_cue_album_is_split_into_tagged_flac_tracks_and_undone(split_album, con, tmp_path):
    lib, img = split_album
    before = {p: p.read_bytes() for p in (img, lib / "HIFI/image.cue")}
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    names = sorted(i["dest_rel"] for i in p["items"] if i["kind"] == "split")
    assert names == ["柏菲/HIFI年代 - 怀念战友/01 - 红星照我去战斗.flac", "柏菲/HIFI年代 - 怀念战友/02 - 怀念战友.flac",
                     "柏菲/HIFI年代 - 怀念战友/03 - 大海啊故乡.flac"]
    assert not [i for i in p["items"] if i["kind"] in ("audio", "cue")]          # no image, no old cue
    assert p["summary"]["split_tracks"] == 3
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 3}, res
    adir = dest / "柏菲/HIFI年代 - 怀念战友"
    tracks = sorted(adir.glob("*.flac"))
    assert b"".join(samples(t) for t in tracks) == samples(img)                   # lossless, nothing lost
    from mutagen.flac import FLAC
    t2 = FLAC(adir / "02 - 怀念战友.flac")
    assert (t2["TITLE"], t2["TRACKNUMBER"], t2["ALBUM"]) == (["怀念战友"], ["2"], ["HIFI年代 - 怀念战友"])
    assert "SPLIT" not in t2
    assert {p: p.read_bytes() for p in before} == before                          # sources untouched
    assert runner.undo(con, res["run"])["actions"] == {"removed": 3}


def test_split_tracks_get_an_album_cue_sheet_when_that_setting_is_on(split_album, con, tmp_path, monkeypatch):
    monkeypatch.setenv("MLT_ALBUM_CUE", "1")
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 4}
    sheet = cue.parse(str(tmp_path / "out/柏菲/HIFI年代 - 怀念战友/HIFI年代 - 怀念战友.cue"))
    assert [f.name for f in sheet.files] == ["01 - 红星照我去战斗.flac", "02 - 怀念战友.flac", "03 - 大海啊故乡.flac"]


def test_a_lossy_image_stays_one_file(tmp_path, con, monkeypatch):
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    noise(lib / "MP3/image.mp3", 6, codec=["-c:a", "libmp3lame"])
    (lib / "MP3/image.cue").write_bytes(CUE.replace("old image name.wav", "image.mp3").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3")
    cid = local.add(con, uid)
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert sorted(i["kind"] for i in p["items"]) == ["audio", "cue"]
    assert p["summary"]["changes"]["kept_image"] == 1


def _run_until_crash(journal: str, point: str, op: str):
    protocol.run_all(protocol.Journal(Path(journal)), crash_at=point, crash_op=op)


@pytest.mark.parametrize("point", ["mid_copy", "after_copy", "after_tag", "after_publish"])
def test_a_split_interrupted_anywhere_resumes_to_the_same_result(split_album, con, tmp_path, point):
    lib, img = split_album
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    method = protocol.choose_method(dest)
    j = protocol.Journal(tmp_path / "j.jsonl")
    for it in p["items"]:
        protocol.plan_op(j, "r1", f"i{it['seq']}", Path(it["src"]), dest / it["dest_rel"], it["kind"],
                         it["codec"], it["tags"], method, it["src_size"], it["src_mtime_ns"])
    crash_op = next(f"i{it['seq']}" for it in p["items"] if it["kind"] == "split")
    # a fresh process (not fork: other tests leave threads behind, and fork() then risks a deadlock)
    import multiprocessing
    child = multiprocessing.get_context("spawn").Process(target=_run_until_crash, args=(str(j.path), point, crash_op))
    child.start()
    child.join(120)
    assert child.exitcode == 137
    protocol.recover(j)
    protocol.run_all(j)
    assert {o["history"][-1] for o in j.ops().values()} == {"committed"}
    files = sorted(x for x in dest.rglob("*") if x.is_file())
    assert len(files) == 3 and not [x for x in files if ".mlt-" in x.name]
    assert b"".join(samples(t) for t in files) == samples(img)


def test_applying_again_finds_the_same_tracks_already_there(split_album, con, tmp_path):
    """Two plans made before either is applied: the second finds the same samples already there."""
    dest = tmp_path / "out"
    first, second = plan.build(con, str(dest), 1), plan.build(con, str(dest), 1)
    runner.apply(con, first, journal_root=tmp_path / "j")
    again = runner.apply(con, second, journal_root=tmp_path / "j")
    assert again["states"] == {"already_existed": 3}, again


def test_audio_before_the_first_track_is_kept_in_it(tmp_path, con, monkeypatch):
    """Codex review on #7: a cue whose first INDEX 01 is not at 0 (a pregap, hidden audio) lost
    the audio before it. The tracks together must cover the whole image."""
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    img = noise(lib / "HTOA/image.flac", 6)
    sheet = CUE.replace("old image name.wav", "image.flac").replace(
        '    INDEX 01 00:00:00\n', '    INDEX 00 00:00:00\n    INDEX 01 00:01:00\n', 1)
    assert "INDEX 01 00:01:00" in sheet
    (lib / "HTOA/image.cue").write_bytes(sheet.encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3")
    cid = local.add(con, uid)
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    dest = tmp_path / "out"
    res = runner.apply(con, plan.build(con, str(dest), 1), journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 3}
    tracks = sorted(dest.rglob("*.flac"))
    assert b"".join(samples(t) for t in tracks) == samples(img)        # nothing before track 1 lost


def test_identical_images_cut_differently_are_not_duplicates(tmp_path, con, monkeypatch):
    """Codex review on #7: the same image in two folders with cue sheets of the same titles but
    other boundaries was copied once -- the other album's tracks silently dropped."""
    import shutil
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    noise(lib / "A/image.flac", 6)
    (lib / "A/image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac").encode("gbk"))
    (lib / "B").mkdir()
    shutil.copy2(lib / "A/image.flac", lib / "B/image.flac")
    (lib / "B/image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac")
                                      .replace("INDEX 01 00:04:00", "INDEX 01 00:03:00").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    for (uid,) in con.execute("SELECT id FROM unit").fetchall():
        con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
        cid = local.add(con, uid)
        con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                    "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert p["summary"]["duplicate_albums_skipped"] == 0
    assert "duplicate_source" not in {i["status"] for i in p["items"]}
    assert "conflict_duplicate" in {i["status"] for i in p["items"]}      # a visible conflict instead


def test_a_cue_sheet_changed_after_the_scan_blocks_the_split(split_album, con, tmp_path):
    """Codex review on #7: the split read the current cue sheet while keeping the titles accepted for
    the old one -- titles on the wrong stretches of audio."""
    lib, _ = split_album
    sheet = lib / "HIFI/image.cue"
    p1 = plan.build(con, str(tmp_path / "out"), 1)                # planned with the scanned cue
    sheet.write_bytes(sheet.read_bytes().replace(b"00:04:00", b"00:03:00"))
    os.utime(sheet, ns=(0, sheet.stat().st_mtime_ns + 10**9))
    p2 = plan.load(con, plan.build(con, str(tmp_path / "out2"), 1))
    assert {i["status"] for i in p2["items"] if i["kind"] == "split"} == {"source_changed"}
    res = runner.apply(con, p1, journal_root=tmp_path / "j")       # the earlier plan, applied now
    assert res["states"] == {"aborted_source_changed": 3}, res
    assert all("cue sheet changed" in f["reason"] for f in res["failed"])
    assert not list((tmp_path / "out").rglob("*.flac"))


def test_finished_files_are_flushed_to_disk_before_they_are_published(split_album, con, tmp_path, monkeypatch):
    """Codex review on #7: a cut (or tagged) file must be on the disk before it gets its final name."""
    flushed, published = [], []
    real_fsync, real_rename = protocol.fsync_file, protocol.rename_excl
    monkeypatch.setattr(protocol, "fsync_file", lambda p: (flushed.append(str(p)), real_fsync(p))[1])
    monkeypatch.setattr(protocol, "rename_excl", lambda a, b: (published.append((str(a), set(flushed))),
                                                                 real_rename(a, b))[1])
    res = runner.apply(con, plan.build(con, str(tmp_path / "out"), 1), journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 3}
    published = [(t, seen) for t, seen in published if ".mlt-probe" not in t]   # not the destination probe
    if not published:
        pytest.skip("this destination does not publish with rename_excl")
    assert len(published) == 3 and all(temp in seen for temp, seen in published)


def test_the_images_lyrics_are_split_along_its_tracks(split_album, con, tmp_path):
    """Codex review on #7: the album's .lrc went next to track 1 only, with album times."""
    lib, _ = split_album
    (lib / "HIFI/image.lrc").write_bytes(
        "[ar:柏菲]\n[ti:整张专辑]\n[00:00.50]第一首\n[00:02.50]第二首 一\n[00:03.20][00:05.10]副歌\n".encode("gbk"))
    scan(con, lib)
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    lrcs = sorted(i["dest_rel"] for i in p["items"] if i["kind"] == "lrc")
    assert lrcs == ["柏菲/HIFI年代 - 怀念战友/01 - 红星照我去战斗.lrc", "柏菲/HIFI年代 - 怀念战友/02 - 怀念战友.lrc",
                    "柏菲/HIFI年代 - 怀念战友/03 - 大海啊故乡.lrc"]
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert set(res["states"]) == {"committed"}, res
    adir = dest / "柏菲/HIFI年代 - 怀念战友"
    read = lambda n: (adir / n).read_text(encoding="utf-8-sig").splitlines()   # noqa: E731
    assert read("01 - 红星照我去战斗.lrc") == ["[ar:柏菲]", "[ti:红星照我去战斗]", "[00:00.50]第一首"]
    assert read("02 - 怀念战友.lrc") == ["[ar:柏菲]", "[ti:怀念战友]", "[00:00.50]第二首 一", "[00:01.20]副歌"]
    assert read("03 - 大海啊故乡.lrc") == ["[ar:柏菲]", "[ti:大海啊故乡]", "[00:01.10]副歌"]
    assert (lib / "HIFI/image.lrc").read_bytes().startswith("[ar:柏菲]".encode("gbk"))   # source untouched


def test_untimed_lyrics_are_kept_as_an_album_file(split_album, con, tmp_path):
    """Codex review on #7: lyrics without time tags could not be split, and were then lost."""
    lib, _ = split_album
    raw = "[ar:柏菲]\n红星照我去战斗\n怀念战友\n".encode("gbk")
    (lib / "HIFI/image.lrc").write_bytes(raw)
    scan(con, lib)
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert [i["dest_rel"] for i in p["items"] if i["kind"] == "lrc"] == ["柏菲/HIFI年代 - 怀念战友/HIFI年代 - 怀念战友.lrc"]
    assert set(runner.apply(con, p["id"], journal_root=tmp_path / "j")["states"]) == {"committed"}
    assert (dest / "柏菲/HIFI年代 - 怀念战友/HIFI年代 - 怀念战友.lrc").read_bytes() == raw      # the original, unchanged


def test_partly_timed_lyrics_are_split_and_also_kept_whole(split_album, con, tmp_path):
    lib, _ = split_album
    (lib / "HIFI/image.lrc").write_bytes("[00:00.50]第一首\n(间奏)\n[00:02.50]第二首\n".encode("gbk"))
    scan(con, lib)
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert sorted(i["dest_rel"].rsplit("/", 1)[1] for i in p["items"] if i["kind"] == "lrc") == [
        "01 - 红星照我去战斗.lrc", "02 - 怀念战友.lrc", "HIFI年代 - 怀念战友.lrc"]


def test_the_lyrics_offset_is_applied_before_lines_are_placed_into_tracks():
    """Codex review on #7: [offset:] changes when a line shows; near a track boundary it decides the track."""
    from music_steward import lrc
    text = "[ar:A]\n[offset:+500]\n[00:02.30]boundary line\n[00:01.00]first\n"
    parts = lrc.split(text, [(0.0, 2.0), (2.0, None)], ["One", "Two"])
    assert parts[0] == "[ar:A]\n[ti:One]\n[00:00.50]first\n[00:01.80]boundary line\n"   # 2.30 - 0.50 = 1.80
    assert parts[1] is None
    assert "[offset" not in parts[0]


JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"cover-bytes" * 40 + b"\xff\xd9"


def test_the_images_embedded_cover_goes_into_every_track(tmp_path, con, monkeypatch):
    """Codex review on #7: only the audio stream was mapped, so a cover embedded in the image was lost."""
    from mutagen.flac import FLAC, Picture
    monkeypatch.setenv("MLT_CUE_ALBUMS", "tracks")
    lib = tmp_path / "lib"
    img = noise(lib / "HIFI/image.flac", 6)
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", JPEG
    f = FLAC(img)
    f.add_picture(pic)
    f.save()
    (lib / "HIFI/image.cue").write_bytes(CUE.replace("old image name.wav", "image.flac").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3")
    cid = local.add(con, uid)
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    dest = tmp_path / "out"
    res = runner.apply(con, plan.build(con, str(dest), 1), journal_root=tmp_path / "j")
    assert set(res["states"]) == {"committed"}, res
    tracks = sorted(dest.rglob("*.flac"))
    assert len(tracks) == 3
    for t in tracks:
        got = FLAC(t).pictures
        assert len(got) == 1 and got[0].data == JPEG and got[0].type == 3
    assert b"".join(samples(t) for t in tracks) == samples(img)          # the audio is still exact


def test_an_ape_tags_cover_art_item_is_found(tmp_path):
    from mutagen.apev2 import APEv2, APEValue, BINARY
    p = tmp_path / "x.ape"
    p.write_bytes(b"MAC " + b"\x00" * 200)
    t = APEv2()
    t["Cover Art (Front)"] = APEValue(b"front.jpg\x00" + JPEG, BINARY)
    t["Title"] = "x"
    t.save(str(p))
    assert split.embedded_picture(str(p), "ape") == ("image/jpeg", JPEG)
    assert split.embedded_picture(str(p), "flac") is None            # wrong codec: not looked for
    assert split.embedded_picture(str(tmp_path / "missing.ape"), "ape") is None
