"""Organize from the album's own info (cue sheet / tags / file and folder names)."""
import json

import pytest

from music_steward.apply import plan
from music_steward.recognize import local, results, units, worker
from music_steward.scanner import scan
from music_steward.server import App

from .conftest import needs_ffmpeg, tag_id3, tone
from .test_cue import image_album  # noqa: F401  (fixture)
from .test_recognize import fake_album

pytestmark = needs_ffmpeg


def test_parse_folder_names():
    assert local.parse_folder("童丽 - 相思 (2008)") == {"artist": "童丽", "album": "相思", "year": 2008}
    assert local.parse_folder("[1998] 真我的風采") == {"artist": None, "album": "真我的風采", "year": 1998}
    assert local.parse_folder("A-ha Hunting High and Low") == {"artist": None, "album": "A-ha Hunting High and Low",
                                                               "year": None}
    assert local.album_folder("Box Set/CD 2") == "Box Set"


@pytest.fixture
def untagged(tmp_path, con):
    """An album folder with no tags at all: everything must come from the names."""
    lib = tmp_path / "lib"
    a = lib / "林子祥 - 誰能明白我 (1985)"
    for i, name in enumerate(["01 - 誰能明白我", "02. 莫再悲", "03 林子祥 - 敢愛敢做"]):
        tone(a / f"{name}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + i)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET status='done' WHERE id=?", (uid,))
    con.commit()
    return lib, uid


def test_untagged_album_from_file_and_folder_names(untagged, con, tmp_path):
    lib, uid = untagged
    cid = local.add(con, uid)
    d = results.unit_detail(con, uid)
    c = next(x for x in d["candidates"] if x["id"] == cid)
    assert (c["album"], c["artist"], c["year"], c["source"]) == ("誰能明白我", "林子祥", 1985, "Local")
    assert [(t["medium_index"], t["title"]) for t in c["tracks"]] == [(1, "誰能明白我"), (2, "莫再悲"), (3, "敢愛敢做")]
    assert c["origin"]["album"] == "folder name" and c["origin"]["titles"] == "file names"
    assert d["confidence"] == "no_match"          # own info never counts as a match
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    audio = sorted(i["dest_rel"] for i in p["items"] if i["kind"] == "audio")
    assert audio == ["林子祥/誰能明白我 (1985)/01 - 誰能明白我.mp3", "林子祥/誰能明白我 (1985)/02 - 莫再悲.mp3",
                     "林子祥/誰能明白我 (1985)/03 - 敢愛敢做.mp3"]
    assert all(i["tags"]["mb_albumid"] is None and i["tags"]["mb_trackid"] is None for i in p["items"])


def test_tags_win_and_garbled_tags_are_repaired(tmp_path, con):
    lib = tmp_path / "lib"
    for i in range(3):
        p = tone(lib / "whatever" / f"{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + i)
        # GBK bytes in Latin-1 frames: the classic garbled Chinese tag
        tag_id3(p, f"歌{i}".encode("gbk").decode("latin-1"), artist="林子祥".encode("gbk").decode("latin-1"),
                album="真的汉子".encode("gbk").decode("latin-1"), encoding=0)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    info = local.describe(con, uid)
    assert (info["album"], info["artist"]) == ("真的汉子", "林子祥")
    assert [t["title"] for t in info["tracks"]] == ["歌0", "歌1", "歌2"] and info["origin"]["titles"] == "tags"


def test_cue_album_from_its_cue_sheet(image_album, con, tmp_path):  # noqa: F811
    u = next(x for x in units.plan_units(con, 1) if x["kind"] == "cue")
    units.enqueue(con, 1, [u])
    uid = con.execute("SELECT id FROM unit WHERE kind='cue'").fetchone()[0]
    con.execute("UPDATE unit SET status='done', n_tracks=3 WHERE id=?", (uid,))
    cid = local.add(con, uid)
    con.execute("INSERT INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    by_kind = {i["kind"]: i for i in p["items"]}
    assert by_kind["audio"]["dest_rel"] == "柏菲/HIFI年代 - 怀念战友/HIFI年代 - 怀念战友.flac"
    rw = by_kind["cue"]["tags"]["cue_rewrite"]
    assert [rw["tracks"][k]["title"] for k in ("1", "2", "3")] == ["红星照我去战斗", "怀念战友", "大海啊故乡"]


def test_local_accept_and_edits_survive_a_rematch(untagged, con, monkeypatch):
    lib, uid = untagged
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    cid = app.use_local(con, {"unit_id": uid})["candidate_id"]
    assert app.decide(con, {"unit_id": uid, "action": "accept", "candidate_id": cid}) == {"ok": True}
    assert app.edit_fields(con, {"unit_id": uid, "overrides": {"album": "誰能明白我 (精選)"}})["ok"]
    assert con.execute("SELECT release_key FROM decision").fetchone()[0] == "local"
    # rebuilding keeps the accept and the edits
    cid2 = app.use_local(con, {"unit_id": uid})["candidate_id"]
    d = con.execute("SELECT * FROM decision").fetchone()
    assert d["candidate_id"] == cid2 and json.loads(d["overrides_json"])["album"] == "誰能明白我 (精選)"
    # so does matching online again
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    assert worker.run_unit(con.execute("PRAGMA database_list").fetchone()[2], uid) == 0
    d = con.execute("SELECT * FROM decision").fetchone()
    srcs = [r[0] for r in con.execute("SELECT source FROM candidate WHERE unit_id=? ORDER BY rank", (uid,))]
    assert srcs == ["MusicBrainz", "MusicBrainz", "Local"]
    assert con.execute("SELECT source FROM candidate WHERE id=?", (d["candidate_id"],)).fetchone()[0] == "Local"
    assert json.loads(d["overrides_json"])["album"] == "誰能明白我 (精選)"
    assert results.units(con, 1)[0]["confidence"] == "ready"   # judged on the online candidates only


def test_clean_names_from_download_sites():
    assert local.clean("未知标题") is None and local.clean("Unknown Artist") is None
    assert local.clean("01 你的眼神", 1) == "你的眼神" and local.clean("1999", 3) == "1999"
    assert local.clean("林子祥.陈奕迅.-.[拉阔演唱会CD2].专辑.(APE)") == "林子祥 陈奕迅 - 拉阔演唱会CD2"
    assert local.clean("Sonata No.9, Op.47") == "Sonata No.9, Op.47"


def test_artist_and_album_from_a_suffix_shared_by_all_file_names(tmp_path, con):
    lib = tmp_path / "lib"
    for i, t in enumerate(["紫菱洲歌", "红豆曲", "晴雯歌"]):
        tone(lib / "rip" / f"0{i + 1}. {t} (郑绪岚 – 情系红楼梦).flac", ["-c:a", "flac"], secs=1, freq=300 + i)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    info = local.describe(con, con.execute("SELECT id FROM unit").fetchone()[0])
    assert (info["artist"], info["album"]) == ("郑绪岚", "情系红楼梦")
    assert [t["title"] for t in info["tracks"]] == ["紫菱洲歌", "红豆曲", "晴雯歌"]
    assert info["origin"]["album"] == "file names"
