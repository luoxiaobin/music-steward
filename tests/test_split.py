"""Setting "Albums of one file with a cue sheet: one file per track": lossless, sample-exact split."""
import json
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
