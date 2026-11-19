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


def _accept_local(con, uid):
    cid = local.add(con, uid)
    con.execute("INSERT OR REPLACE INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, 'local')", (uid, cid))
    con.commit()
    return cid


@pytest.fixture
def two_disc_cues(tmp_path, con):
    """经典老歌101 CD1 and CD2: two cue image albums in sibling folders."""
    from .test_cue import CUE
    lib = tmp_path / "lib"
    for n in (1, 2):
        a = lib / f"经典老歌101 CD{n}"
        tone(a / "image.flac", ["-c:a", "flac"], secs=6, freq=300 + n)
        (a / "image.cue").write_bytes(CUE.replace("old image name.wav", "image.wav")
                                      .replace("HIFI年代 - 怀念战友", f"经典老歌101 CD{n}").encode("gbk"))
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["kind"] == "cue"])
    con.execute("UPDATE unit SET status='done', n_tracks=3")
    con.commit()
    return lib, [r[0] for r in con.execute("SELECT id FROM unit ORDER BY folder")]


def test_discs_of_a_set_share_one_album_folder(two_disc_cues, con, tmp_path):
    lib, uids = two_disc_cues
    for uid in uids:
        _accept_local(con, uid)
    info = local.describe(con, uids[1])
    assert (info["album"], info["mediums"], info["origin"]["disc"]) == ("经典老歌101", 2, "album title")
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert all(i["status"] == "ok" for i in p["items"])
    got = sorted((i["dest_rel"], i["tags"].get("disc")) for i in p["items"])
    assert got == [("柏菲/经典老歌101/经典老歌101 (CD 1).cue", None), ("柏菲/经典老歌101/经典老歌101 (CD 1).flac", "1/2"),
                   ("柏菲/经典老歌101/经典老歌101 (CD 2).cue", None), ("柏菲/经典老歌101/经典老歌101 (CD 2).flac", "2/2")]


def test_cue_track_titles_and_disc_can_be_edited(two_disc_cues, con, tmp_path):
    lib, uids = two_disc_cues
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    _accept_local(con, uids[0])
    assert "error" in app.edit_fields(con, {"unit_id": uids[0], "overrides": {"disc": "3", "discs": "2"}})
    assert "error" in app.edit_fields(con, {"unit_id": uids[0], "overrides": {"disc": "x"}})
    r = app.edit_fields(con, {"unit_id": uids[0], "overrides": {
        "album": "经典老歌一百零一首", "disc": "1", "discs": "3", "cue_titles": {"2": "怀念战友 (电影《冰山上的来客》插曲)", "9": "x"}}})
    assert r["overrides"]["cue_titles"] == {"2": "怀念战友 (电影《冰山上的来客》插曲)"}
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1, "经典老歌101 CD1"))
    img = next(i for i in p["items"] if i["kind"] == "audio")
    assert img["dest_rel"] == "柏菲/经典老歌一百零一首/经典老歌一百零一首 (CD 1).flac" and img["tags"]["disc"] == "1/3"
    rw = next(i for i in p["items"] if i["kind"] == "cue")["tags"]["cue_rewrite"]
    assert rw["tracks"]["2"]["title"] == "怀念战友 (电影《冰山上的来客》插曲)" and rw["tracks"]["1"]["title"] == "红星照我去战斗"


def test_file_albums_set_as_discs_get_disc_prefixed_names(tmp_path, con):
    lib = tmp_path / "lib"
    for n in (1, 2):
        for i in range(2):
            tone(lib / f"Set part {n}" / f"0{i + 1} - Song {n}{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + 10 * n + i)
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    for n, uid in enumerate([r[0] for r in con.execute("SELECT id FROM unit ORDER BY folder")], 1):
        con.execute("UPDATE unit SET status='done' WHERE id=?", (uid,))
        _accept_local(con, uid)
        app.edit_fields(con, {"unit_id": uid, "overrides": {"album": "The Set", "albumartist": "Band", "disc": str(n)}})
    p = plan.load(con, plan.build(con, str(tmp_path / "out"), 1))
    assert sorted(i["dest_rel"] for i in p["items"]) == [
        "Band/The Set/1-01 - Song 10.mp3", "Band/The Set/1-02 - Song 11.mp3",
        "Band/The Set/2-01 - Song 20.mp3", "Band/The Set/2-02 - Song 21.mp3"]
    assert {i["tags"]["disc"] for i in p["items"]} == {"1/2", "2/2"}


def test_bulk_own_info_accept_and_skip_touch_only_undecided(two_disc_cues, con, monkeypatch):
    lib, uids = two_disc_cues
    # a third album: matched online (Ready) -- bulk own-info actions must leave it alone
    for i in range(3):
        tag_id3(tone(lib / "Album A" / f"{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=500 + i), f"A{i}", album="Album A")
    scan(con, lib)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    ready = con.execute("SELECT id FROM unit WHERE folder='Album A'").fetchone()[0]
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    worker.run_unit(con.execute("PRAGMA database_list").fetchone()[2], ready)
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    root = str(lib)
    # the user already skipped CD2 by hand: bulk actions must not change that
    app.decide(con, {"unit_id": uids[1], "action": "skip"})
    assert app.bulk(con, {"root": root, "action": "local"}) == {"done": 1, "failed": [], "n_failed": 0}
    us = {u["id"]: u for u in results.units(con, 1)}
    assert us[uids[0]]["local"]["album"] == "经典老歌101" and us[ready]["local"] is None and us[uids[1]]["local"] is None
    assert app.bulk(con, {"root": root, "action": "local"})["done"] == 0          # idempotent
    assert app.bulk(con, {"root": root, "action": "accept_local"})["done"] == 1
    d = con.execute("SELECT * FROM decision WHERE unit_id=?", (uids[0],)).fetchone()
    assert d["action"] == "accept" and d["release_key"] == "local"
    assert "error" in app.bulk(con, {"root": root, "action": "skip", "confidences": ["ready"]})
    assert app.bulk(con, {"root": root, "action": "skip", "confidences": ["no_match", "weak"]})["done"] == 0
    assert con.execute("SELECT action FROM decision WHERE unit_id=?", (uids[1],)).fetchone()[0] == "skip"
    assert con.execute("SELECT 1 FROM decision WHERE unit_id=?", (ready,)).fetchone() is None
    assert "error" in app.bulk(con, {"root": "/elsewhere", "action": "local"})
