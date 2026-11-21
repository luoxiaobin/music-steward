"""The retire / restore API (ADR 0003): opt-in, dry run, confirmation, jobs, and what the plan view shows."""
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from music_steward import retire
from music_steward.server import App, make_handler

from .conftest import needs_ffmpeg
from .test_apply import accepted  # noqa: F401  (fixture needed by `applied`)
from .test_retire import ALBUM, applied  # noqa: F401  (fixture: an applied run, retiring allowed)
from .test_server import login, req

pytestmark = needs_ffmpeg
H = {"X-MLT": "1"}


@pytest.fixture
def api(applied, tmp_path):  # noqa: F811
    lib, dest, run = applied
    app = App(str(tmp_path / "app.sqlite"), [str(lib)])
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ck = login(app, port_ref["port"])
    port = port_ref["port"]

    def get(path):
        return json.loads(req(port, path, cookie=ck)[2])

    def post(path, body):
        return json.loads(req(port, path, cookie=ck, method="POST", headers=H, body=json.dumps(body))[2])

    def wait():
        for _ in range(300):
            if not app.scan_state.get("running"):
                return app.scan_state["result"]
            time.sleep(0.05)
        raise AssertionError("job did not finish")
    yield lib, dest, run, get, post, wait
    httpd.shutdown()


def test_settings_list_the_collection_folders_and_save_the_opt_in(api):
    lib, dest, run, get, post, wait = api
    s = get("/api/settings")
    assert [(r["path"], r["allowed"]) for r in s["retire_roots"]] == [(str(lib), True)]
    rid = s["retire_roots"][0]["id"]
    s = post("/api/settings", {"settings": {}, "retire_roots": {str(rid): False}})
    assert s["retire_roots"][0]["allowed"] is False
    assert "unknown collection folder" in post("/api/settings", {"settings": {}, "retire_roots": {"999": True}})["error"]


def test_the_dry_run_changes_nothing(api):
    lib, dest, run, get, post, wait = api
    before = sorted(p.name for p in (lib / ALBUM).iterdir())
    d = get(f"/api/retire_plan?run={run}")
    assert (d["albums_ok"], d["files_ok"], d["allowed"]) == (1, 4, True)
    assert sorted(p.name for p in (lib / ALBUM).iterdir()) == before and not (lib / retire.ORIGINALS).exists()
    assert "error" in get("/api/retire_plan?run=999")


def test_retiring_needs_the_confirmation(api):
    lib, dest, run, get, post, wait = api
    assert "confirmation" in post("/api/retire", {"id": run})["error"]
    assert "confirmation" in post("/api/retire", {"id": run, "ack": "yes"})["error"]
    assert not (lib / retire.ORIGINALS).exists()


def test_retire_then_restore_through_the_api(api):
    lib, dest, run, get, post, wait = api
    assert post("/api/retire", {"id": run, "ack": True}) == {"started": True}
    res = wait()
    assert (res["status"], res["albums"], res["files"]) == ("complete", 1, 4), res
    assert not list((lib / ALBUM).iterdir())


def test_the_plan_view_lists_the_originals_and_restore_brings_them_back(api, con):
    lib, dest, run, get, post, wait = api
    pid = con.execute("SELECT plan_id FROM run WHERE id=?", (run,)).fetchone()[0]
    assert get(f"/api/plan/{pid}")["originals"] == [] and get(f"/api/plan/{pid}")["retire_allowed"] is True
    post("/api/retire", {"id": run, "ack": True})
    wait()
    o = get(f"/api/plan/{pid}")["originals"]
    assert len(o) == 1 and o[0]["moved"] == 4 and o[0]["summary"]["albums"] == 1 and o[0]["status"] == "complete"
    post("/api/restore", {"id": o[0]["id"]})
    res = wait()
    assert res["restored"] == 4 and res["blocked"] == []
    assert len(list((lib / ALBUM).iterdir())) == 4
    assert get(f"/api/plan/{pid}")["originals"][0]["restored_at"]


def test_a_collection_folder_without_the_opt_in_refuses_the_job(api, con):
    from music_steward import settings
    lib, dest, run, get, post, wait = api
    settings.set_retire_allowed(con, 1, False)
    post("/api/retire", {"id": run, "ack": True})
    res = wait()
    assert res["status"] == "error" and "not allowed" in res["abort_reason"]
    assert len(list((lib / ALBUM).iterdir())) == 4 and not (lib / retire.ORIGINALS).exists()


def _batch_through_the_api(api, con):
    lib, dest, run, get, post, wait = api
    pid = con.execute("SELECT plan_id FROM run WHERE id=?", (run,)).fetchone()[0]
    post("/api/retire", {"id": run, "ack": True})
    wait()
    return pid, get(f"/api/plan/{pid}")["originals"][0]["id"]


def test_the_archive_preview_and_job(api, con):
    lib, dest, run, get, post, wait = api
    pid, bid = _batch_through_the_api(api, con)
    backup = str(dest.parent / "backup")
    assert "overlaps" in get(f"/api/archive_plan?batch={bid}&target={lib}/backup")["error"]
    pre = get(f"/api/archive_plan?batch={bid}&target={backup}")
    assert pre["error"] is None and pre["files"] == 6 and pre["same_drive"] is True
    assert post("/api/archive", {"id": bid, "target": backup}) == {"started": True}
    res = wait()
    assert (res["status"], res["verified"], res["files"]) == ("complete", True, 6), res
    b = get(f"/api/plan/{pid}")["originals"][0]
    assert b["archive"]["verified"] is True and b["archive"]["dest"].endswith(b["key"])
    assert "api_key" not in str(b) and "archive_json" not in b
    post("/api/archive", {"id": bid, "target": str(lib / "backup")})
    assert wait()["status"] == "error"                                      # the job refuses it too


def test_the_delete_confirmation_is_enforced_on_the_server(api, con):
    from music_steward import settings
    lib, dest, run, get, post, wait = api
    settings.save(con, {"retire_cooloff_days": "0"})
    pid, bid = _batch_through_the_api(api, con)
    pre = get(f"/api/delete_plan?batch={bid}")
    assert (pre["albums_ok"], pre["files_ok"], pre["needs_no_backup_ack"], pre["has_archive"]) == (1, 4, True, False)
    parked = sorted(p for p in (lib / retire.ORIGINALS).rglob("*") if p.is_file())
    for body, text in (({"id": bid}, "DELETE"), ({"id": bid, "typed": "delete"}, "DELETE"),
                       ({"id": bid, "typed": "DELETE"}, "no verified archive"),
                       ({"id": bid, "typed": "DELETE", "ack_no_backup": "yes"}, "no verified archive"),
                       ({"id": bid, "typed": "DELETE", "ack_no_backup": 1}, "no verified archive")):
        post("/api/delete", body)
        res = wait()
        assert res["status"] == "error" and text in res["abort_reason"], (body, res)
        assert sorted(p for p in (lib / retire.ORIGINALS).rglob("*") if p.is_file()) == parked    # nothing deleted
    post("/api/delete", {"id": bid, "typed": "DELETE", "ack_no_backup": True})
    res = wait()
    assert (res["status"], res["deleted"], res["kept"]) == ("complete", 4, [])
    b = get(f"/api/plan/{pid}")["originals"][0]
    assert b["deleted"] == 4 and b["moved"] == 0 and b["delete"]["deleted"] == 4


def test_a_young_batch_needs_the_played_them_tick_even_with_an_archive(api, con):
    lib, dest, run, get, post, wait = api
    pid, bid = _batch_through_the_api(api, con)                             # cooling-off stays at its default, 7 days
    post("/api/archive", {"id": bid, "target": str(dest.parent / "backup")})
    assert wait()["verified"] is True
    pre = get(f"/api/delete_plan?batch={bid}")
    assert pre["young"] is True and pre["has_archive"] is True and pre["cooloff_days"] == 7
    post("/api/delete", {"id": bid, "typed": "DELETE"})
    assert "played the organized copies" in wait()["abort_reason"]
    post("/api/delete", {"id": bid, "typed": "DELETE", "ack_checked": True})
    assert wait()["deleted"] == 4
