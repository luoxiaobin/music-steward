"""History: one readable line per action that changed copies, originals or a collection folder's path."""
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from music_steward import cli, collection, history, settings
from music_steward.apply import plan

from .conftest import needs_ffmpeg
from .test_apply import accepted  # noqa: F401  (fixture)
from .test_collection import moved
from .test_server import login, req

pytestmark = needs_ffmpeg


@pytest.fixture
def api(accepted, con, tmp_path):  # noqa: F811
    from music_steward.server import App, make_handler
    lib, dest = accepted
    settings.set_retire_allowed(con, 1, True)
    settings.save(con, {"retire_cooloff_days": "0"})
    app = App(str(tmp_path / "app.sqlite"), [])
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ck = login(app, port_ref["port"])
    port = port_ref["port"]

    def get(path):
        return json.loads(req(port, path, cookie=ck)[2])

    def post(path, body):
        r = json.loads(req(port, path, cookie=ck, method="POST", headers={"X-MLT": "1"}, body=json.dumps(body))[2])
        for _ in range(300):
            if not app.scan_state.get("running"):
                return r, app.scan_state.get("result")
            time.sleep(0.05)
        raise AssertionError("job did not finish")
    yield lib, dest, get, post
    httpd.shutdown()


def test_each_action_on_copies_and_originals_is_one_line(api, con, tmp_path):
    lib, dest, get, post = api
    assert get("/api/history") == []
    uid = con.execute("SELECT id FROM unit").fetchone()[0]
    pid = plan.build(con, str(dest), 1)
    post("/api/apply", {"id": pid})
    run = con.execute("SELECT max(id) FROM run").fetchone()[0]
    post("/api/retire", {"id": run, "ack": True})
    bid = con.execute("SELECT max(id) FROM retire_batch").fetchone()[0]
    post("/api/archive", {"id": bid, "target": str(tmp_path / "backup")})
    post("/api/restore", {"id": bid})
    post("/api/retire", {"id": run, "ack": True})
    bid2 = con.execute("SELECT max(id) FROM retire_batch").fetchone()[0]
    _, res = post("/api/delete", {"id": bid2, "typed": "DELETE", "ack_no_backup": True})
    assert res["status"] == "complete", res
    lines = get("/api/history")
    assert [x["message"] for x in lines] == [
        "Deleted 4 parked original files",
        "Set aside the originals of 1 albums (4 files)",
        "Restored 4 original files",
        "Archived 6 parked original files (verified)",
        "Set aside the originals of 1 albums (4 files)",
        f"Copied 4 files (run #{run})"]
    assert all(x["albums"] == ["夜航"] and x["n_albums"] == 1 and x["root"] == str(lib) for x in lines)
    assert lines[3]["dest"].startswith(str(tmp_path / "backup"))
    assert [x["kind"] for x in get(f"/api/unit/{uid}")["history"]] == [x["kind"] for x in lines]
    for t in history.MESSAGES.values():                                     # every message can be translated
        from music_steward import i18n
        assert t in {r[0] for r in i18n.TABLE}, t


def test_an_undo_and_a_path_change_are_lines_too_and_errors_are_not(api, con, tmp_path):
    lib, dest, get, post = api
    pid = plan.build(con, str(dest), 1)
    post("/api/apply", {"id": pid})
    run = con.execute("SELECT max(id) FROM run").fetchone()[0]
    post("/api/undo", {"id": run})
    new = moved(lib, tmp_path)
    collection.set_path(con, 1, str(new))
    _, res = post("/api/apply", {"id": pid})                                 # refused: made before the change
    assert res["status"] == "error"
    lines = get("/api/history")
    assert [x["message"] for x in lines] == ["The collection folder's path changed", f"Undid run #{run}: 4 copies removed",
                                            f"Copied 4 files (run #{run})"]
    assert (lines[0]["old_path"], lines[0]["new_path"], lines[0]["albums"]) == (str(lib), str(new), [])
    assert get("/api/history?root_id=999") == []


def test_the_command_line_records_its_runs(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    db = str(tmp_path / "app.sqlite")
    pid = plan.build(con, str(dest), 1)
    cli.main(["--db", db, "apply", str(pid)])
    run = con.execute("SELECT max(id) FROM run").fetchone()[0]
    cli.main(["--db", db, "undo", str(run)])
    assert [x["kind"] for x in history.lines(con)] == ["run_undone", "run_applied"]


def test_a_history_line_that_cannot_be_written_never_fails_the_action(con):
    con.execute("DROP TABLE event_unit")
    assert history.record(con, "restored", 1, [1], batch_id=1, detail={"n": 1}) is None


def test_album_names_and_paths_in_the_history_are_data():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    body = js[js.index("function historyList("):js.index("async function openHistory(")]
    for needle in ("e.old_path", "e.dest", "e.albums.join"):
        lines = [ln for ln in body.splitlines() if needle in ln and "h(" in ln]
        assert lines and all("data-no-translate" in ln for ln in lines), needle
