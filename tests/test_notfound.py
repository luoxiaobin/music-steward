"""Removing a not-found album: only on the owner's word, only the app's record, checked again at that moment."""
import json
import shutil
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from music_steward import history, notfound, retire
from music_steward.recognize import results, units
from music_steward.scanner import scan

from .conftest import needs_ffmpeg
from .test_apply import accepted, snapshot  # noqa: F401  (fixture)
from .test_retire import ALBUM, _recognize_folder, applied  # noqa: F401  (fixture)
from .test_server import login, req

pytestmark = needs_ffmpeg


@pytest.fixture
def gone(accepted, con):  # noqa: F811
    """The accepted album's folder went away (moved elsewhere); a scan marked its files gone."""
    lib, dest = accepted
    away = lib.parent / "elsewhere"
    shutil.move(str(lib / ALBUM), str(away))
    scan(con, lib)
    _recognize_folder(con)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    assert results.units(con, 1)[0]["presence"] == "not_found"
    return lib, away, uid


def test_remove_deletes_only_the_record_and_history_keeps_the_name(gone, con, tmp_path):
    lib, away, uid = gone
    before = {**snapshot(lib), **snapshot(away)}
    assert [(x["id"], x["label"], x["decision"]) for x in notfound.removable(con, 1)] == [(uid, "夜航", "accept")]
    assert notfound.removable(con, 1, "another folder") == []
    r = notfound.remove(con, 1, [uid])
    assert r == {"removed": 1, "kept": []}
    for table in ("unit", "decision", "candidate", "cand_track"):
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert {**snapshot(lib), **snapshot(away)} == before                    # no file anywhere touched
    line = history.lines(con)[0]
    assert (line["message"], line["albums"]) == ("Removed 1 not-found albums from the app (no file touched)", ["夜航"])


def test_the_files_coming_back_later_make_a_new_album(gone, con):
    lib, away, uid = gone
    notfound.remove(con, 1, [uid])
    shutil.move(str(away), str(lib / ALBUM))
    scan(con, lib)
    units.enqueue(con, 1, units.plan_units(con, 1))
    us = results.units(con, 1)
    assert len(us) == 1 and us[0]["presence"] == "here" and us[0]["decision"] is None
    # SQLite may hand the new album the removed album's id: the old album's History must not become the new one's
    assert history.lines(con, unit_id=us[0]["id"]) == []
    assert history.lines(con)[0]["albums"] == ["夜航"]


def test_each_album_is_checked_again_at_the_moment_of_removal(gone, con):
    lib, away, uid = gone
    shutil.move(str(away), str(lib / ALBUM))                                # the files came back
    scan(con, lib)
    r = notfound.remove(con, 1, [uid, 99999])
    assert r["removed"] == 0 and [k["reason"] for k in r["kept"]] == [notfound.MESSAGES["here"], notfound.MESSAGES["unknown"]]
    assert con.execute("SELECT count(*) FROM decision").fetchone()[0] == 1


def test_an_album_whose_originals_are_set_aside_is_never_removed(applied, con):  # noqa: F811
    lib, dest, run = applied
    retire.retire(con, run)
    scan(con, lib)
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    assert notfound.removable(con, 1) == []
    r = notfound.remove(con, 1, [uid])
    assert r["removed"] == 0 and r["kept"][0]["reason"] == notfound.MESSAGES["retired"]


def test_refused_when_the_folder_is_not_connected_or_a_job_runs(gone, con):
    lib, away, uid = gone
    with pytest.raises(notfound.RemoveError, match="running job"):
        notfound.remove(con, 1, [uid], {"running": True, "kind": "recognize"})
    shutil.move(str(lib), str(lib.parent / "unmounted"))
    with pytest.raises(notfound.RemoveError, match="not connected"):
        notfound.remove(con, 1, [uid])
    assert con.execute("SELECT count(*) FROM unit").fetchone()[0] == 1


def test_the_api_needs_the_named_albums_and_the_confirmation(gone, tmp_path):
    from music_steward.server import App, make_handler
    lib, away, uid = gone
    app = App(str(tmp_path / "app.sqlite"), [])
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ck = login(app, port_ref["port"])

        def get(path):
            return json.loads(req(port_ref["port"], path, cookie=ck)[2])

        def post(body):
            return json.loads(req(port_ref["port"], "/api/remove_not_found", cookie=ck, method="POST",
                                  headers={"X-MLT": "1"}, body=json.dumps(body))[2])
        assert get(f"/api/unit/{uid}")["presence"] == "not_found"
        assert [x["id"] for x in get("/api/not_found?root_id=1")] == [uid]
        assert "name the albums" in post({"root_id": 1, "confirm": True})["error"]
        assert "name the albums" in post({"root_id": 1, "unit_ids": "all", "confirm": True})["error"]
        assert "confirmation" in post({"root_id": 1, "unit_ids": [uid]})["error"]
        assert "confirmation" in post({"root_id": 1, "unit_ids": [uid], "confirm": "yes"})["error"]
        app.scan_state = {"running": True, "kind": "scan"}
        assert "running job" in post({"root_id": 1, "unit_ids": [uid], "confirm": True})["error"]
        app.scan_state = {"running": False}
        assert post({"root_id": 1, "unit_ids": [uid], "confirm": True}) == {"removed": 1, "kept": []}
        assert get("/api/not_found?root_id=1") == []
    finally:
        httpd.shutdown()
    time.sleep(0)


def test_the_album_folder_in_the_not_found_banner_is_data():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    start = js.index('if (d.presence === "not_found")')
    banner = js[start:js.index("Remove…", start)]
    assert 'h("span", { class: "mono", "data-no-translate": "" }, d.folder)' in banner


def test_folder_wide_bulk_actions_leave_a_not_found_album_as_it_was(gone, con, tmp_path):
    """They act on undecided albums the owner can see in the folder; one whose files are gone is not decided for them."""
    from music_steward.server import App
    lib, away, uid = gone
    con.execute("DELETE FROM decision")
    con.commit()
    app = App(con.execute("PRAGMA database_list").fetchone()[2], [str(lib)], None)
    for body in ({"action": "skip", "confidences": ["no_match", "weak", "needs_review"]}, {"action": "local"},
                 {"action": "accept_local"}):
        r = app.bulk(con, {"root": str(lib), **body})
        assert r["done"] == 0 and r["n_failed"] == 0, (body, r)
    con.execute("UPDATE candidate SET distance=0.01, fingerprint_agrees=2")         # a Ready album
    con.commit()
    assert app.bulk_accept(con, {"root": str(lib), "confidences": ["ready", "likely"]}) == {"accepted": 0}
    assert con.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
    assert app.bulk(con, {"root": str(lib), "action": "skip", "unit_ids": [uid]})["done"] == 1   # ticked: as asked
