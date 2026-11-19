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
