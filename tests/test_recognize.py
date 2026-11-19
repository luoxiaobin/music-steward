"""Recognition plumbing without the network: units, queue state, persistence, confidence."""
import json
import shutil

import pytest

from music_steward.recognize import results, units, worker
from music_steward.scanner import scan

from .conftest import needs_ffmpeg, tag_id3, tone

pytestmark = needs_ffmpeg


@pytest.fixture
def music(tmp_path):
    lib = tmp_path / "lib"
    # a 2-disc album: CD 1 / CD 2 sub-folders with different album tags
    for d, n in (("CD 1", 2), ("CD 2", 2)):
        for i in range(n):
            p = tone(lib / "Box Set" / d / f"{i + 1:02d}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=300 + i)
            tag_id3(p, f"T{i}", album=f"Box {d}")
    # an ordinary album folder
    for i in range(3):
        tag_id3(tone(lib / "Album A" / f"{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=500 + i), f"A{i}",
                album="Album A")
    # a folder of singles: one album with 3 files, and 3 strays
    for i in range(3):
        tag_id3(tone(lib / "Singles" / f"x{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=700 + i), f"X{i}",
                album="Big Album")
    for i, alb in enumerate(["One", "Two", None]):
        tag_id3(tone(lib / "Singles" / f"s{i}.mp3", ["-c:a", "libmp3lame"], secs=1, freq=900 + i), f"S{i}",
                album=alb)
    return lib


def test_units_follow_album_rules(music, con):
    scan(con, music)
    us = {(u["kind"], u["label"]): len(u["file_ids"]) for u in units.plan_units(con, 1)}
    assert us[("album", "Box Set")] == 4          # discs merged
    assert us[("album", "Album A")] == 3
    assert us[("album", "Big Album")] == 3        # singles folder: big group stays an album
    assert sum(1 for k in us if k[0] == "track") == 3


def test_enqueue_is_idempotent_and_resumable(music, con):
    scan(con, music)
    us = units.plan_units(con, 1)
    first = units.enqueue(con, 1, us)
    assert first["queued"] == len(us)
    con.execute("UPDATE unit SET status='done'")
    again = units.enqueue(con, 1, us)
    assert again["already_done"] == len(us) and again["requeued"] == 0
    assert units.enqueue(con, 1, us, requeue=True)["requeued"] == len(us)


def fake_album(paths, key, search=None):
    return {"kind": "album", "recommendation": "medium", "acoustid_used": True,
            "files_fingerprint_linked": len(paths), "candidates": [
                {"rank": 0, "source": "MusicBrainz", "album_id": "a1", "album": "Album A", "artist": "Artist",
                 "year": 2001, "country": "XW", "label": "L", "media": "CD", "mediums": 1, "distance": 0.08,
                 "penalties": [["album", 0.01]], "extra_items": [], "extra_tracks": [],
                 "fingerprint_agrees": len(paths), "n_mapped": len(paths), "recording_ids": len(paths),
                 "tracks": [{"path": p, "recording_id": f"r{i}", "release_track_id": f"t{i}", "index": i + 1,
                             "medium": 1, "medium_index": i + 1, "title": f"A{i}", "artist": "Artist",
                             "length": 1.0, "track_distance": 0.0} for i, p in enumerate(paths)]},
                {"rank": 1, "source": "MusicBrainz", "album_id": "a2", "album": "Other", "artist": "X",
                 "year": None, "country": None, "label": None, "media": None, "mediums": None,
                 "distance": 0.6, "penalties": [], "extra_items": [], "extra_tracks": [],
                 "fingerprint_agrees": 0, "n_mapped": 0, "recording_ids": 0, "tracks": []}]}


def test_worker_persists_candidates_and_mapping(music, con, tmp_path, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    dbp = con.execute("PRAGMA database_list").fetchone()[2]
    assert worker.run_unit(dbp, uid) == 0
    d = results.unit_detail(con, uid)
    assert d["status"] == "done" and [c["album"] for c in d["candidates"]] == ["Album A", "Other"]
    assert len(d["candidates"][0]["tracks"]) == 3
    assert d["confidence"] == "ready"           # all fingerprints agree, distance 0.08


def test_worker_records_errors(music, con, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, units.plan_units(con, 1)[:1])
    from music_steward.recognize import beets_adapter

    def boom(paths, key, search=None):
        raise RuntimeError("musicbrainz is down")
    monkeypatch.setattr(beets_adapter, "match_album", boom)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    dbp = con.execute("PRAGMA database_list").fetchone()[2]
    assert worker.run_unit(dbp, uid) == 1
    row = con.execute("SELECT status, error FROM unit").fetchone()
    assert row["status"] == "error" and "musicbrainz is down" in row["error"]


@pytest.mark.parametrize("dist,fp,n,expect", [
    (0.08, 10, 11, "ready"), (0.11, 2, 11, "needs_review"), (0.30, 9, 11, "needs_review"),
    (0.72, 0, 11, "no_match"), (0.72, 3, 11, "needs_review"),
    (0.17, 25, 25, "ready"), (0.17, 14, 25, "needs_review"), (0.13, 0, 1, "needs_review")])
def test_confidence_rule(dist, fp, n, expect):
    assert results.confidence({"status": "done", "n_files": n},
                              {"distance": dist, "fingerprint_agrees": fp}) == expect


def test_garbled_tags_are_repaired_in_memory_only(tmp_path):
    from music_steward.recognize import beets_adapter as A
    from beets.library import Item
    p = tone(tmp_path / "g.mp3", ["-c:a", "libmp3lame"], secs=1)
    tag_id3(p, "人海中一個你".encode("gbk").decode("latin-1"), artist="林子祥".encode("gbk").decode("latin-1"),
            album="Lam 2004 XRCD", encoding=0)
    before = p.read_bytes()
    it = Item.from_path(str(p))
    assert A._repair([it]) == 1
    assert (it.title, it.artist, it.album) == ("人海中一個你", "林子祥", "Lam 2004 XRCD")
    assert p.read_bytes() == before                    # nothing written to the file


def test_clean_tags_are_not_touched(tmp_path):
    from music_steward.recognize import beets_adapter as A
    from beets.library import Item
    p = tone(tmp_path / "c.mp3", ["-c:a", "libmp3lame"], secs=1)
    tag_id3(p, "Motörhead", artist="Björk")
    it = Item.from_path(str(p))
    assert A._repair([it]) == 0 and it.title == "Motörhead"


def _accept_top(con, uid):
    from music_steward.recognize.results import release_key
    cid = con.execute("SELECT id FROM candidate WHERE unit_id=? AND rank=0", (uid,)).fetchone()[0]
    con.execute("INSERT OR REPLACE INTO decision(unit_id, action, candidate_id, decided_at, release_key) "
                "VALUES(?, 'accept', ?, 0, ?)", (uid, cid, release_key(con, cid)))
    con.commit()


def test_accept_survives_rematch_when_release_still_there(music, con, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    dbp = con.execute("PRAGMA database_list").fetchone()[2]
    worker.run_unit(dbp, uid)
    _accept_top(con, uid)

    def reordered(paths, key, search=None):  # same release comes back at rank 1 after re-match
        r = fake_album(paths, key)
        r["candidates"].reverse()
        for i, c in enumerate(r["candidates"]):
            c["rank"] = i
        return r
    monkeypatch.setattr(beets_adapter, "match_album", reordered)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"], requeue=True)
    worker.run_unit(dbp, uid)
    d = results.unit_detail(con, uid)
    chosen = next(c for c in d["candidates"] if c["id"] == d["decision"]["candidate_id"])
    assert d["decision"]["action"] == "accept" and chosen["album_id"] == "a1" and not d["notice"]


def test_accept_dropped_with_notice_when_release_gone(music, con, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    dbp = con.execute("PRAGMA database_list").fetchone()[2]
    worker.run_unit(dbp, uid)
    _accept_top(con, uid)

    def other(paths, key, search=None):
        r = fake_album(paths, key)
        r["candidates"] = r["candidates"][1:]
        r["candidates"][0]["rank"] = 0
        return r
    monkeypatch.setattr(beets_adapter, "match_album", other)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"], requeue=True)
    worker.run_unit(dbp, uid)
    d = results.unit_detail(con, uid)
    assert d["decision"] is None and "no longer among the candidates" in d["notice"]


def test_skip_survives_rematch(music, con, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    from music_steward.recognize import beets_adapter
    monkeypatch.setattr(beets_adapter, "match_album", fake_album)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    dbp = con.execute("PRAGMA database_list").fetchone()[2]
    worker.run_unit(dbp, uid)
    con.execute("INSERT INTO decision(unit_id, action, decided_at) VALUES(?, 'skip', 0)", (uid,))
    con.commit()
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"], requeue=True)
    worker.run_unit(dbp, uid)
    assert results.unit_detail(con, uid)["decision"]["action"] == "skip"


def test_fingerprint_confirmed_candidate_wins_a_distance_tie():
    from music_steward.recognize.beets_adapter import _rerank
    cands = [{"rank": 0, "source": "Discogs", "distance": 0.170, "fingerprint_agrees": 0},
             {"rank": 1, "source": "MusicBrainz", "distance": 0.172, "fingerprint_agrees": 25},
             {"rank": 2, "source": "MusicBrainz", "distance": 0.60, "fingerprint_agrees": 25}]
    out = _rerank(cands, 25)
    assert [c["source"] for c in out][:2] == ["MusicBrainz", "Discogs"] and [c["rank"] for c in out] == [0, 1, 2]
    assert out[2]["distance"] == 0.60     # a bonus never lifts a far-off release above a close one
