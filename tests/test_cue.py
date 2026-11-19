"""Cue-sheet image albums: one long audio file + a cue sheet listing its tracks."""
import json
from pathlib import Path

import pytest

from music_steward import cue
from music_steward.apply import plan, runner
from music_steward.recognize import units
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tone

pytestmark = needs_ffmpeg

CUE = '''PERFORMER "柏菲"
TITLE "HIFI年代 - 怀念战友"
FILE "old image name.wav" WAVE
  TRACK 01 AUDIO
    TITLE "红星照我去战斗"
    PERFORMER "柏菲"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "怀念战友"
    INDEX 00 00:01:00
    INDEX 01 00:02:00
  TRACK 03 AUDIO
    TITLE "大海啊故乡"
    INDEX 01 00:04:00
'''


def test_parse_gbk_cue_with_lengths():
    c = cue.parse_bytes(CUE.encode("gbk"))
    assert c.encoding in ("gb18030", "gbk", "gb2312") and c.performer == "柏菲" and c.title == "HIFI年代 - 怀念战友"
    f = cue.with_lengths(c.files[0], 6.0)
    assert [t.title for t in f.tracks] == ["红星照我去战斗", "怀念战友", "大海啊故乡"]
    assert [t.start for t in f.tracks] == [0.0, 2.0, 4.0] and [t.length for t in f.tracks] == [2.0, 2.0, 2.0]


def test_rewrite_keeps_encoding_and_everything_else():
    raw = CUE.encode("gbk")
    out, enc = cue.rewrite_bytes(raw, {"old image name.wav": "怀念战友.flac"},
                                 {"title": "HIFI年代1 · 怀念战友", "performer": "Various Artists"},
                                 {2: {"title": "怀念战友 (歌剧《天山上的来客》插曲)"}})
    text = out.decode(enc)
    assert enc != "utf-8-sig" and 'FILE "怀念战友.flac" WAVE' in text
    assert 'TITLE "HIFI年代1 · 怀念战友"' in text and 'PERFORMER "Various Artists"' in text
    assert 'TITLE "怀念战友 (歌剧《天山上的来客》插曲)"' in text and 'TITLE "红星照我去战斗"' in text
    assert "INDEX 00 00:01:00" in text and text.count("TRACK") == 3


def test_gb18030_cue_stays_gb18030_even_for_new_characters():
    # GB18030 covers all of Unicode, so a GBK cue never needs the UTF-8 fallback
    out, enc = cue.rewrite_bytes(CUE.encode("gbk"), album={"title": "劉德華 ★ Ünïcödé"})
    assert enc.startswith("gb") and "★" in out.decode(enc)


def test_big5_cue_falls_back_to_utf8_bom_when_new_text_does_not_fit():
    big5 = 'TITLE "懷念戰友"\nFILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    TITLE "紅星照我去戰鬥"\n    INDEX 01 00:00:00\n'.encode("big5")
    out, enc = cue.rewrite_bytes(big5, album={"title": "怀念战友 (简体)"})   # 怀/战 are not in Big5
    text = out.decode(enc)
    assert 'TITLE "怀念战友 (简体)"' in text and "紅星照我去戰鬥" in text
    if not cue.detect_encoding(big5).startswith("gb"):
        assert enc == "utf-8-sig" and out.startswith(b"\xef\xbb\xbf")


@pytest.fixture
def image_album(tmp_path, con):
    lib = tmp_path / "lib"
    a = lib / "HIFI"
    img = tone(a / "image.flac", ["-c:a", "flac"], secs=6)
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").encode("gbk"))
    (a / "cover.jpg").write_bytes(b"\xff\xd8cover")
    # a normal album next door with a per-track cue (one FILE per track) must stay a normal album
    b = lib / "Normal"
    for i in range(3):
        tone(b / f"0{i + 1}.flac", ["-c:a", "flac"], secs=1, freq=500 + i)
    (b / "album.cue").write_text("".join(f'FILE "0{i + 1}.flac" WAVE\n  TRACK 0{i + 1} AUDIO\n    INDEX 01 00:00:00\n'
                                         for i in range(3)), encoding="utf-8")
    scan(con, lib)
    return lib, img


def test_image_album_detected_and_per_track_cue_ignored(image_album, con):
    us = {u["kind"]: u for u in units.plan_units(con, 1)}
    assert us["cue"]["label"] == "HIFI年代 - 怀念战友" and len(us["cue"]["file_ids"]) == 1
    assert us["album"]["folder"] == "Normal" and len(us["album"]["file_ids"]) == 3


def test_cue_album_apply_copies_image_and_rewritten_cue_then_undo(image_album, con, tmp_path):
    lib, img = image_album
    u = next(x for x in units.plan_units(con, 1) if x["kind"] == "cue")
    units.enqueue(con, 1, [u])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
    cid = con.execute("INSERT INTO candidate(unit_id, rank, source, album_id, album, artist, year, mediums, distance, "
                      "fingerprint_agrees, n_mapped) VALUES(?,0,'MusicBrainz','rel-1','HIFI年代1 · 怀念战友',"
                      "'Various Artists',2002,1,0.38,0,3)", (uid,)).lastrowid
    for n, t in enumerate(["红星照我去战斗", "怀念战友", "大海啊故乡"], 1):
        con.execute("INSERT INTO cand_cue_track VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, n, t, f"rec{n}", None, n, 1, n, t, "柏菲", 2.0, 0.1))
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at) VALUES(?, 'accept', ?, 0)", (uid, cid))
    con.commit()
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert sorted((i["kind"], i["dest_rel"]) for i in p["items"]) == [
        ("audio", "Various Artists/HIFI年代1 · 怀念战友 (2002)/HIFI年代1 · 怀念战友.flac"),
        ("cue", "Various Artists/HIFI年代1 · 怀念战友 (2002)/HIFI年代1 · 怀念战友.cue"),
        ("image", "Various Artists/HIFI年代1 · 怀念战友 (2002)/cover.jpg")]
    before = (lib / "HIFI/image.cue").read_bytes()
    res = runner.apply(con, p["id"], journal_root=tmp_path / "j")
    assert res["states"] == {"committed": 3}, res
    adir = dest / "Various Artists/HIFI年代1 · 怀念战友 (2002)"
    new_cue = cue.parse(str(adir / "HIFI年代1 · 怀念战友.cue"))
    assert new_cue.encoding == "utf-8-sig"          # UTF-8 + BOM: macOS players misread GBK cues
    assert (adir / "HIFI年代1 · 怀念战友.cue").read_bytes().startswith(b"\xef\xbb\xbf")
    assert new_cue.files[0].name == "HIFI年代1 · 怀念战友.flac" and new_cue.title == "HIFI年代1 · 怀念战友"
    assert [t.title for t in new_cue.tracks] == ["红星照我去战斗", "怀念战友", "大海啊故乡"]
    from mutagen.flac import FLAC
    tags = FLAC(adir / "HIFI年代1 · 怀念战友.flac")
    assert tags["ALBUM"] == ["HIFI年代1 · 怀念战友"] and tags["MUSICBRAINZ_ALBUMID"] == ["rel-1"]
    assert (lib / "HIFI/image.cue").read_bytes() == before            # source cue untouched
    assert runner.undo(con, res["run"])["actions"] == {"removed": 3}


def test_detection_falls_back_to_gb18030_before_latin1():
    """charset-normalizer gave up on a short rewritten GB18030 cue; it must not be read as Latin-1."""
    raw = 'FILE "林子祥创作＋流行歌集.ape" WAVE\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'.encode("gb18030")
    assert cue.parse_bytes(raw).files[0].name == "林子祥创作＋流行歌集.ape"


def test_cue_can_keep_its_original_encoding(monkeypatch):
    from music_steward.apply import plan as planmod
    monkeypatch.setenv("MLT_CUE_ENCODING", "original")
    assert planmod.cue_encoding() is None
    out, enc = cue.rewrite_bytes(CUE.encode("gbk"), {"old image name.wav": "x.flac"}, out_encoding=None)
    assert enc.startswith("gb") and 'FILE "x.flac"' in out.decode(enc)


def test_catalogue_latin_artist_gives_way_to_the_albums_own_chinese_name(image_album, con, tmp_path):
    """George Lam (Discogs) vs 林子祥 (the cue sheet): one artist must get one folder."""
    u = next(x for x in units.plan_units(con, 1) if x["kind"] == "cue")
    units.enqueue(con, 1, [u])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
    cid = con.execute("INSERT INTO candidate(unit_id, rank, source, album, artist, year, mediums, distance) "
                      "VALUES(?, 0, 'Discogs', 'Huai Nian', 'Pai Fei =', 2002, 1, 0.2)", (uid,)).lastrowid
    for n in (1, 2, 3):
        con.execute("INSERT INTO cand_cue_track(candidate_id, cue_track, title, artist, medium, medium_index) "
                    "VALUES(?,?,?,?,1,?)", (cid, n, f"T{n}", "Pai Fei =" if n < 3 else "Guest Singer", n))
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at) VALUES(?, 'accept', ?, 0)", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    img = next(i for i in p["items"] if i["kind"] == "audio")
    assert img["dest_rel"].startswith("柏菲/Huai Nian (2002)/") and img["tags"]["albumartist"] == "柏菲"
    tr = next(i for i in p["items"] if i["kind"] == "cue")["tags"]["cue_rewrite"]["tracks"]
    assert [tr[k]["performer"] for k in ("1", "2", "3")] == ["柏菲", "柏菲", "Guest Singer"]


def test_identical_album_in_two_folders_is_copied_once(tmp_path, con):
    """李烁 好梦留人 sits in two folders of the NAS: byte-identical copies are copied once;
    a different album under the same name stays a conflict."""
    import shutil
    lib = tmp_path / "lib"
    a = lib / "A"
    tone(a / "image.flac", ["-c:a", "flac"], secs=6)
    (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").encode("gbk"))
    shutil.copytree(a, lib / "B")                                  # the same album again
    c = lib / "C"
    tone(c / "image.flac", ["-c:a", "flac"], secs=6, freq=600)     # same name, different audio
    (c / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    from music_steward.recognize import local
    for (uid,) in con.execute("SELECT id FROM unit").fetchall():
        con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
        cid = local.add(con, uid)
        con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                    "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    st = {}
    for i in p["items"]:
        st.setdefault(i["src"].split("/lib/")[1].split("/")[0], set()).add(i["status"])
    assert st["B"] == {"duplicate_source"} and p["summary"]["duplicate_albums_skipped"] == 1
    assert st["A"] == {"conflict_duplicate"} and st["C"] == {"conflict_duplicate"}   # A vs C: really different
