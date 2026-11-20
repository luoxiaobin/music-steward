"""Settings: stored in the app DB, validated, overridden by the environment, used by plans."""
import json

import pytest

from music_steward import settings
from music_steward.apply import plan
from music_steward.server import App

from .test_apply import accepted  # noqa: F401  (fixture)
from .conftest import needs_ffmpeg


def test_defaults_save_validate_and_env_override(con, monkeypatch):
    for k in ("MLT_SCRIPT", "MLT_YEAR", "MLT_CUE_ENCODING", "MLT_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    assert settings.get(con, "script") == "simplified" and settings.get(con, "year") == "original"
    settings.save(con, {"script": "Traditional", "sources": "deezer, musicbrainz", "dest": "~/Organized"})
    assert settings.get(con, "script") == "traditional"
    assert settings.get(con, "sources") == "musicbrainz,deezer"            # MusicBrainz always first, order fixed
    assert settings.get(con, "dest").endswith("/Organized") and not settings.get(con, "dest").startswith("~")
    with pytest.raises(settings.SettingsError):
        settings.save(con, {"script": "klingon"})
    with pytest.raises(settings.SettingsError):
        settings.save(con, {"sources": "spotify"})
    monkeypatch.setenv("MLT_SCRIPT", "keep")
    v = settings.all_values(con)["script"]
    assert v["value"] == "keep" and v["overridden_by"] == "MLT_SCRIPT"


@needs_ffmpeg
def test_plan_follows_the_settings(accepted, con, monkeypatch):
    lib, dest = accepted
    monkeypatch.delenv("MLT_SCRIPT", raising=False)
    monkeypatch.delenv("MLT_YEAR", raising=False)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    cid = con.execute("SELECT candidate_id FROM decision WHERE unit_id=?", (uid,)).fetchone()[0]
    year = con.execute("SELECT year FROM candidate WHERE id=?", (cid,)).fetchone()[0]
    con.execute("UPDATE candidate SET artist='鄧麗君', original_year=? WHERE id=?", (year - 10, cid))
    con.commit()
    first = lambda d: next(i for i in plan.load(con, plan.build(con, str(d), 1))["items"] if i["kind"] == "audio")
    it = first(dest)
    assert it["dest_rel"].startswith("邓丽君/") and f"({year - 10})" in it["dest_rel"]
    settings.save(con, {"script": "keep", "year": "release"})
    it = first(str(dest) + "2")
    assert it["dest_rel"].startswith("鄧麗君/") and f"({year})" in it["dest_rel"]


def test_settings_endpoint_refuses_a_destination_inside_the_collection(con, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    con.execute("INSERT INTO root(path, added_at) VALUES(?, 0)", (str(lib),))
    con.commit()
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    assert "error" in app.save_settings(con, {"settings": {"dest": str(lib / "Organized")}})
    r = app.save_settings(con, {"settings": {"dest": str(tmp_path / "out"), "album_cue": "off"}})
    assert r["settings"]["album_cue"]["value"] in ("off",) and r["settings"]["dest"]["value"] == str(tmp_path / "out")
    assert set(r["status"]) == {"acoustid", "discogs"} and json.dumps(r).count("token") == 0
