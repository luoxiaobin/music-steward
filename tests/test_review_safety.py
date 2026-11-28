"""Safer review: a candidate whose title and artist (or volume number) are another album's is flagged, never Likely,
never accepted in bulk, and accepted alone only after an explicit confirmation. The first trial on a real collection
accepted 8 of 19 albums under another album's name and kept them through a second review. (Names here are made up.)"""
import json

import pytest

from music_steward.recognize import results
from music_steward.server import App

from .test_trial import _album


def _cand(con, uid, album, artist, *, distance=0.12, fp=0, rank=0, source="MusicBrainz"):
    n = len(json.loads(con.execute("SELECT file_ids FROM unit WHERE id=?", (uid,)).fetchone()[0]))
    cid = con.execute("INSERT INTO candidate(unit_id, rank, source, album_id, album, artist, year, mediums, distance, "
                      "fingerprint_agrees, n_mapped) VALUES(?,?,?,?,?,?,2001,1,?,?,?)",
                      (uid, rank, source, f"alb-{uid}-{rank}", album, artist, distance, fp, n)).lastrowid
    con.execute("UPDATE unit SET status='done' WHERE id=?", (uid,))
    con.commit()
    return cid


@pytest.fixture
def music(con):
    con.execute("INSERT INTO root(path, added_at) VALUES('/music', 0)")
    con.commit()
    return con


def _unit(con, uid):
    return next(u for u in results.units(con, 1) if u["id"] == uid)


def test_another_artist_and_another_title_is_a_different_album(music):
    uid = _album(music, "戏曲/粤剧选段", 6, artist="红线女")
    _cand(music, uid, "Lontano da qui", "Ape")
    u = _unit(music, uid)
    assert u["evidence"]["artist"] == "differs" and u["evidence"]["different_album"]
    assert u["confidence"] == "no_match"                     # was Likely: close on paper, every track mapped


def test_the_audio_confirming_it_wins_over_the_names(music):
    uid = _album(music, "戏曲/粤剧选段", 6, artist="红线女")
    _cand(music, uid, "Lontano da qui", "Ape", distance=0.05, fp=6)
    assert _unit(music, uid)["confidence"] == "ready"


@pytest.mark.parametrize("ours, theirs", [("關正傑", "关正杰"), ("Beyoncé", "Beyonce"), ("歌手甲 & 歌手乙", "歌手乙")])
def test_one_script_no_accents_and_each_singer_count_as_the_same_artist(music, ours, theirs):
    uid = _album(music, "Pop/Some Album", 8, artist=ours)
    _cand(music, uid, "Another Name Entirely", theirs)
    u = _unit(music, uid)
    assert u["evidence"]["artist"] == "agrees" and not u["evidence"]["different_album"]
    assert u["confidence"] == "likely"


def test_various_artists_agree_with_a_compilation(music):
    uid = _album(music, "合辑/金曲精选", 10, artists=["歌手甲", "歌手乙", "歌手丙", "歌手丁"])
    _cand(music, uid, "Hits of the Year", "Various Artists")
    assert _unit(music, uid)["evidence"]["artist"] == "agrees"
    uid2 = _album(music, "合辑/群星 - 老歌", 10, artist="群星")
    _cand(music, uid2, "Old Songs", "群星")
    assert _unit(music, uid2)["evidence"]["artist"] == "agrees"


def test_nothing_to_compare_with_is_not_a_warning(music):
    uid = _album(music, "unknown/folder", 6, artist="")
    _cand(music, uid, "Whatever", "Somebody")
    u = _unit(music, uid)
    assert u["evidence"]["artist"] == "unknown" and not u["evidence"]["different_album"]


def test_another_volume_of_the_same_series_is_a_different_album(music):
    uid = _album(music, "发烧/测试天碟5ape", 8, artist="群星", label="测试天碟5")
    _cand(music, uid, "测试天碟4", "群星")
    u = _unit(music, uid)
    assert u["evidence"]["numbers_differ"] and u["evidence"]["different_album"] and u["confidence"] == "no_match"
    assert results._volume_numbers("精选.cd2") == {"2"} and results._volume_numbers("Greatest Hits CD2") == {"2"}
    assert results._volume_numbers("Live 1994") == set()     # a year is not a volume


def test_garbled_artist_tags_are_compared_repaired(music):
    uid = _album(music, "mp3/x", 6, artist="ºìÏßÅ®",
                 issues=[{"field": "artist", "proposal": "红线女"}])
    _cand(music, uid, "Lontano da qui", "红线女")
    assert _unit(music, uid)["evidence"]["artist"] == "agrees"


def test_own_info_is_never_a_different_album(music):
    uid = _album(music, "戏曲/粤剧选段", 6, artist="红线女")
    cid = _cand(music, uid, "Totally Else", "Nobody", source="Local")
    d = results.unit_detail(music, uid)
    assert not next(c for c in d["candidates"] if c["id"] == cid)["evidence"]["different_album"]


@pytest.fixture
def app(music, tmp_path):
    return App(music.execute("PRAGMA database_list").fetchone()[2], ["/music"])


def test_accepting_one_asks_first_then_accepts(music, app):
    uid = _album(music, "戏曲/粤剧选段", 6, artist="红线女")
    cid = _cand(music, uid, "Lontano da qui", "Ape")
    r = app.decide(music, {"unit_id": uid, "action": "accept", "candidate_id": cid})
    assert r["confirm"] == "different_album" and r["why"]["artist"] == "Ape" and r["why"]["label"] == "粤剧选段"
    assert music.execute("SELECT COUNT(*) FROM decision").fetchone()[0] == 0
    assert app.decide(music, {"unit_id": uid, "action": "accept", "candidate_id": cid, "confirm_different": True}) \
        == {"ok": True}
    u = _unit(music, uid)
    assert u["decision"]["source"] == "MusicBrainz" and u["decision"]["album"] == "Lontano da qui"


def test_a_right_match_is_accepted_without_a_question(music, app):
    uid = _album(music, "Pop/Phil Collins - Hits", 8, artist="Phil Collins")
    cid = _cand(music, uid, "Hits", "Phil Collins")
    assert app.decide(music, {"unit_id": uid, "action": "accept", "candidate_id": cid}) == {"ok": True}


def test_bulk_actions_leave_a_different_album_to_the_owner(music, app):
    bad = _album(music, "戏曲/粤剧选段", 6, artist="红线女")
    _cand(music, bad, "Lontano da qui", "Ape", distance=0.05)
    good = _album(music, "Pop/Phil Collins - Hits", 8, artist="Phil Collins")
    _cand(music, good, "Hits", "Phil Collins", distance=0.05)
    assert app.bulk_accept(music, {"root": "/music", "confidences": ["ready", "likely"]}) == {"accepted": 1}
    assert {r[0] for r in music.execute("SELECT unit_id FROM decision")} == {good}
    music.execute("DELETE FROM decision")
    music.commit()
    r = app.bulk(music, {"root": "/music", "action": "accept_top", "unit_ids": [bad, good]})
    assert r["done"] == 1 and "different album" in r["failed"][0]
    assert {r[0] for r in music.execute("SELECT unit_id FROM decision")} == {good}
