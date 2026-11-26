"""Search again (terms or a MusicBrainz link) and Edit fields."""
import json

import pytest

from music_steward.apply import plan
from music_steward.recognize import search, units, worker
from music_steward.scanner import scan

from .conftest import needs_ffmpeg
from .test_apply import accepted  # noqa: F401  (fixture)
from .test_recognize import fake_album, music  # noqa: F401  (fixtures/helpers)

pytestmark = needs_ffmpeg
REL = "https://musicbrainz.org/release/6f8c2f3e-1b2a-4c5d-9e0f-123456789abc"
REC = "https://musicbrainz.org/recording/0a1b2c3d-4e5f-6789-abcd-ef0123456789"


def test_parse_terms_and_links():
    assert search.parse("album", "劉德華", "真我的風采") == {"artist": "劉德華", "album": "真我的風采"}
    assert search.parse("track", "童丽", "月亮代表我的心") == {"artist": "童丽", "title": "月亮代表我的心"}
    assert search.parse("album", link=REL)["ids"] == ["6f8c2f3e-1b2a-4c5d-9e0f-123456789abc"]
    assert search.parse("track", link=REC)["ids"] == ["0a1b2c3d-4e5f-6789-abcd-ef0123456789"]
    assert search.parse("album", link="6F8C2F3E-1B2A-4C5D-9E0F-123456789ABC")["ids"][0].islower()
    for bad in [("album", "", "x", ""), ("album", "a", "", ""), ("album", "", "", REC), ("track", "", "", REL),
                ("album", "", "", "https://example.com/x")]:
        with pytest.raises(search.SearchError):
            search.parse(*bad)


def test_worker_passes_the_search_to_the_matcher(music, con, monkeypatch):
    scan(con, music)
    units.enqueue(con, 1, [u for u in units.plan_units(con, 1) if u["label"] == "Album A"])
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    con.execute("UPDATE unit SET search_json=? WHERE id=?", (json.dumps({"artist": "X", "album": "Y"}), uid))
    con.commit()
    seen = {}
    from music_steward.recognize import beets_adapter

    def spy(paths, key, s=None):
        seen["search"] = s
        return fake_album(paths, key)
    monkeypatch.setattr(beets_adapter, "match_album", spy)
    assert worker.run_unit(con.execute("PRAGMA database_list").fetchone()[2], uid) == 0
    assert seen["search"] == {"artist": "X", "album": "Y"}


def test_edits_change_plan_tags_and_paths(accepted, con):
    lib, dest = accepted
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    fid = con.execute("SELECT id FROM file WHERE relpath LIKE '%02 - x.mp3'").fetchone()[0]
    con.execute("UPDATE decision SET overrides_json=? WHERE unit_id=?",
                (json.dumps({"album": "夜航 (重制版)", "albumartist": "周海晴", "year": 2011,
                             "titles": {str(fid): "夜航 (Live)"}}, ensure_ascii=False), uid))
    con.commit()
    p = plan.load(con, plan.build(con, str(dest), 1))
    by_file = {i["file_id"]: i for i in p["items"] if i["kind"] == "audio"}
    it = by_file[fid]
    assert it["dest_rel"] == "周海晴/夜航 (重制版) (2011)/02 - 夜航 (Live).mp3"
    assert it["tags"]["title"] == "夜航 (Live)" and it["tags"]["album"] == "夜航 (重制版)" and it["tags"]["date"] == 2011
    other = next(i for f, i in by_file.items() if f != fid)
    assert other["tags"]["title"] == "序曲"                 # untouched track keeps the release title


def test_original_year_is_used_and_an_edit_still_wins(accepted, con):
    lib, dest = accepted
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    cid = con.execute("SELECT candidate_id FROM decision WHERE unit_id=?", (uid,)).fetchone()[0]
    year = con.execute("SELECT year FROM candidate WHERE id=?", (cid,)).fetchone()[0]
    con.execute("UPDATE candidate SET original_year=? WHERE id=?", (year - 20, cid))   # matched a reissue
    con.commit()
    p = plan.load(con, plan.build(con, str(dest), 1))
    it = next(i for i in p["items"] if i["kind"] == "audio")
    assert f"({year - 20})" in it["dest_rel"] and it["tags"]["date"] == year - 20
    con.execute("UPDATE decision SET overrides_json=? WHERE unit_id=?", (json.dumps({"year": 1999}), uid))
    con.commit()
    p = plan.load(con, plan.build(con, str(dest) + "2", 1))
    assert "(1999)" in next(i for i in p["items"] if i["kind"] == "audio")["dest_rel"]


def test_names_written_in_simplified_unless_the_user_typed_them(accepted, con, monkeypatch):
    lib, dest = accepted
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    cid = con.execute("SELECT candidate_id FROM decision WHERE unit_id=?", (uid,)).fetchone()[0]
    con.execute("UPDATE candidate SET artist='鄧麗君', album='勢不兩立' WHERE id=?", (cid,))
    con.commit()
    it = next(i for i in plan.load(con, plan.build(con, str(dest), 1))["items"] if i["kind"] == "audio")
    assert it["dest_rel"].startswith("邓丽君/势不两立") and it["tags"]["albumartist"] == "邓丽君"
    monkeypatch.setenv("MLT_SCRIPT", "keep")
    it = next(i for i in plan.load(con, plan.build(con, str(dest) + "2", 1))["items"] if i["kind"] == "audio")
    assert it["dest_rel"].startswith("鄧麗君/勢不兩立")
    monkeypatch.delenv("MLT_SCRIPT")
    con.execute("UPDATE decision SET overrides_json=? WHERE unit_id=?", (json.dumps({"albumartist": "鄧麗君"}), uid))
    con.commit()
    it = next(i for i in plan.load(con, plan.build(con, str(dest) + "3", 1))["items"] if i["kind"] == "audio")
    assert it["dest_rel"].startswith("鄧麗君/势不两立")         # typed by the user: kept as typed


def test_per_track_artist_is_written_to_the_copy_and_the_album_cue(accepted, con, monkeypatch):
    """A duet or a compilation: one track's artist differs from the album artist. Typed by the user: kept as typed."""
    from music_steward.server import App
    lib, dest = accepted
    monkeypatch.setenv("MLT_ALBUM_CUE", "1")
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    fid = con.execute("SELECT id FROM file WHERE relpath LIKE '%02 - x.mp3'").fetchone()[0]
    r = app.edit_fields(con, {"unit_id": uid, "overrides": {"artists": {str(fid): " 周海晴 & 鄧麗君 ", "99999": "x", "abc": "y"}}})
    assert r["overrides"] == {"artists": {str(fid): "周海晴 & 鄧麗君"}}       # only this album's files; trimmed
    p = plan.load(con, plan.build(con, str(dest), 1))
    by_file = {i["file_id"]: i for i in p["items"] if i["kind"] == "audio"}
    assert by_file[fid]["tags"]["artist"] == "周海晴 & 鄧麗君"                 # not converted to Simplified
    assert by_file[fid]["tags"]["albumartist"] == "周海晴"                    # the album artist is unchanged
    assert next(i for f, i in by_file.items() if f != fid)["tags"]["artist"] == "周海晴"
    assert by_file[fid]["dest_rel"].startswith("周海晴/")                      # the folder follows the album artist
    sheet = next(i for i in p["items"] if i["kind"] == "cue_new")["tags"]["generated_text"]
    assert 'PERFORMER "周海晴 & 鄧麗君"' in sheet
