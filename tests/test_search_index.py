"""Search: any part of a name, Simplified/Traditional-insensitive, repaired tags, pinyin."""
from music_steward import search
from music_steward.recognize import units
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tag_id3, tone

pytestmark = needs_ffmpeg


def test_pinyin_and_normalize():
    assert search.pinyin("鄧麗君") == search.pinyin("邓丽君") == ("denglijun", "dlj")
    assert search.pinyin("HIFI年代6") == ("hifiniandai6", "hifind6")
    assert search.normalize("鄧麗君 - 勢不兩立") == "邓丽君势不两立"


def test_find_albums_folders_tracks(tmp_path, con):
    lib = tmp_path / "lib"
    for i, t in enumerate(["我只在乎你", "甜蜜蜜"]):
        tag_id3(tone(lib / "鄧麗君-精選" / f"0{i + 1}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + i), t,
                artist="鄧麗君", album="精選")
    # a garbled tag (GBK bytes in a Latin-1 frame) must be found by its real text
    tag_id3(tone(lib / "other" / "x.mp3", ["-c:a", "libmp3lame"], secs=1), "怀念战友".encode("gbk").decode("latin-1"),
            artist="x", album="y", encoding=0)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    for q in ("邓丽君", "鄧麗君", "denglijun", "deng li jun", "dlj"):
        r = search.query(con, q)
        assert r["counts"] == {"albums": 1, "folders": 1, "tracks": 2}, (q, r["counts"])
        assert r["folders"][0]["folder"] == "鄧麗君-精選" and r["folders"][0]["n"] == 2
    r = search.query(con, "甜蜜")
    assert [t["label"] for t in r["tracks"]] == ["甜蜜蜜"]
    assert [t["label"] for t in search.query(con, "怀念战友")["tracks"]] == ["怀念战友"]
    assert [t["label"] for t in search.query(con, "hnzy")["tracks"]] == ["怀念战友"]
    assert search.query(con, "")["counts"] == {}


def test_index_follows_decisions(tmp_path, con):
    lib = tmp_path / "lib"
    for i in range(3):
        tag_id3(tone(lib / "folder" / f"{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + i), f"T{i}", album="Album A")
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    assert search.query(con, "Edited Name")["counts"]["albums"] == 0
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    cid = con.execute("INSERT INTO candidate(unit_id, rank, source, album, artist) VALUES(?, 0, 'MusicBrainz', 'Album A', 'Z')",
                      (uid,)).lastrowid
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, overrides_json) "
                "VALUES(?, 'accept', ?, 1, '{\"album\": \"Edited Name\"}')", (uid, cid))
    con.commit()
    r = search.query(con, "edited name")              # the user's own edit is searchable at once
    assert r["counts"]["albums"] == 1 and "Accepted" in r["albums"][0]["sub"]
