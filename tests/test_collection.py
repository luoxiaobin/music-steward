"""Collection folders (ADR 0004, phase 1): not connected, and Change path -- the same collection at a new path."""
import json
import shutil
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from music_steward import cli, collection, folders, retire, settings
from music_steward.apply import plan, runner
from music_steward.scanner import scan

from .conftest import needs_ffmpeg
from .test_apply import accepted, snapshot  # noqa: F401  (fixture)
from .test_retire import applied  # noqa: F401  (fixture)
from .test_server import login, req

pytestmark = needs_ffmpeg


def moved(lib, tmp_path, name="music-1"):
    """The same collection, as it appears after the share is mounted under a new name."""
    new = tmp_path / name
    shutil.copytree(lib, new, copy_function=shutil.copy2)
    return new


def test_change_path_keeps_albums_and_decisions_and_switches_retire_off(applied, con, tmp_path):  # noqa: F811
    lib, dest, run = applied
    decisions = con.execute("SELECT * FROM decision").fetchall()
    new = moved(lib, tmp_path)
    before = snapshot(new)
    c = collection.check(con, 1, str(new))
    assert c["ok"] and c["checked"] == c["matched"] == 4 and c["result"]["code"] == "accepted"
    assert "Checked 4 files: 4 are at the new path" in c["result"]["message"]
    r = collection.set_path(con, 1, str(new))
    assert r["changed"] and con.execute("SELECT path FROM root WHERE id=1").fetchone()[0] == str(new)
    assert settings.retire_allowed(con, 1) is False                          # allowed for the old place only
    h = collection.history(con, 1)
    assert [(x["old_path"], x["new_path"], x["checked"], x["matched"]) for x in h] == [(str(lib), str(new), 4, 4)]
    assert snapshot(new) == before                                           # nothing read or written there
    # the next scan re-reads nothing: same relative paths, sizes and times; the decision is still there
    s = scan(con, str(new))
    assert s.root_id == 1 and s.counts["n_new"] == 0 and s.counts["n_removed"] == 0
    assert [dict(d) for d in con.execute("SELECT * FROM decision")] == [dict(d) for d in decisions]


def test_a_different_collection_is_refused_with_no_override(applied, con, tmp_path):  # noqa: F811
    lib, dest, run = applied
    other = tmp_path / "other"
    shutil.copytree(lib, other)
    for p in list(other.rglob("*.mp3")):
        p.write_bytes(b"x")                                                  # same names, other files
    c = collection.check(con, 1, str(other))
    assert not c["ok"] and c["result"]["code"] == "refused" and (c["checked"], c["matched"]) == (4, 2)
    assert "Only 2 of 4 files were found" in c["result"]["message"]
    with pytest.raises(collection.CollectionError, match="does not look like the same collection"):
        collection.set_path(con, 1, str(other))
    assert con.execute("SELECT path FROM root WHERE id=1").fetchone()[0] == str(lib)
    assert collection.history(con, 1) == []


def test_same_path_missing_registered_and_overlapping_folders_are_refused(applied, con, tmp_path):  # noqa: F811
    lib, dest, run = applied
    assert collection.check(con, 1, str(lib))["result"]["code"] == "same_path"
    assert collection.check(con, 1, str(tmp_path / "nowhere"))["result"]["code"] == "missing"
    assert collection.check(con, 1, "smb://nas/music")["result"]["code"] == "folder"
    second = moved(lib, tmp_path, "second")
    scan(con, str(second))                                                   # a collection folder of its own
    assert collection.check(con, 1, str(second))["result"]["code"] == "already_registered"
    assert collection.check(con, 1, str(second / "Ò¹º½"))["result"]["code"] == "overlaps"
    assert collection.check(con, 1, str(tmp_path))["result"]["code"] == "overlaps"     # contains both


def test_parked_originals_block_the_change(applied, con, tmp_path):  # noqa: F811
    lib, dest, run = applied
    retire.retire(con, run)
    new = moved(lib, tmp_path)
    c = collection.check(con, 1, str(new))
    assert not c["ok"] and [b["code"] for b in c["blockers"]] == ["parked"]
    with pytest.raises(collection.CollectionError, match="Restore or delete them first"):
        collection.set_path(con, 1, str(new))
    bid = con.execute("SELECT id FROM retire_batch").fetchone()[0]
    retire.restore(con, bid)
    assert [b["code"] for b in collection.check(con, 1, str(new))["blockers"]] == []


def test_an_unfinished_copy_or_a_running_job_blocks_the_change(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    con.execute("INSERT INTO run(plan_id, run_key, journal, method, started_at) VALUES(?, 'k', 'j', 'm', 0)", (pid,))
    con.commit()                                                             # interrupted: never finished
    new = moved(lib, tmp_path)
    c = collection.check(con, 1, str(new), {"running": True, "kind": "scan"})
    assert [(b["code"], b["detail"]) for b in c["blockers"]] == [("job_running", "scan"), ("unfinished_run", "#1")]
    con.execute("UPDATE run SET undone_at=1")                                # undone runs touch only the destination
    assert collection.check(con, 1, str(new))["ok"]


def test_a_plan_made_before_the_change_is_never_applied(accepted, con, tmp_path):  # noqa: F811
    """A plan holds the old full paths, which may later be another share: Apply and Retry refuse it."""
    lib, dest = accepted
    pid = plan.build(con, str(dest), 1)
    collection.set_path(con, 1, str(moved(lib, tmp_path)))
    shutil.rmtree(lib)
    lib.mkdir()                                                              # something else at the old path
    assert runner.apply(con, pid, journal_root=tmp_path / "j") == {"error": runner.MOVED}
    assert not dest.exists()
    res = runner.apply(con, plan.build(con, str(dest), 1), journal_root=tmp_path / "j")   # a new plan works
    assert res["states"] == {"committed": 4}, res


def test_retire_still_works_for_a_run_copied_before_the_change(applied, con, tmp_path):  # noqa: F811
    """It moves the originals found at the NEW path, after checking their identity; the old path is untouched."""
    lib, dest, run = applied
    new = moved(lib, tmp_path)
    collection.set_path(con, 1, str(new))
    old_before = snapshot(lib)
    with pytest.raises(retire.RetireError, match="not allowed"):
        retire.retire(con, run)                                              # off again after the change
    settings.set_retire_allowed(con, 1, True)
    res = retire.retire(con, run)
    assert res["albums"] == 1, res
    assert (new / retire.ORIGINALS).is_dir() and not (new / "Ò¹º½" / "01 - x.mp3").exists()
    assert snapshot(lib) == old_before and not (lib / retire.ORIGINALS).exists()


def test_not_connected_and_the_same_share_mounted_elsewhere(applied, con, tmp_path, monkeypatch):  # noqa: F811
    lib, dest, run = applied
    mnt = tmp_path / "mnt"
    monkeypatch.setattr(folders, "_mounted_shares", lambda: [("nas.local", "music", str(tmp_path))])
    collection.remember_share(con, 1, str(lib))
    row = con.execute("SELECT share_host, share_name, share_sub FROM root").fetchone()
    assert tuple(row) == ("nas.local", "music", "lib")                       # never a user name or password
    assert collection.describe(con, 1)["connected"] and collection.suggestion(con, 1) is None
    new = moved(lib, mnt)                                                    # mnt/music-1 ...
    shutil.move(str(new), str(mnt / "lib"))
    shutil.rmtree(lib)                                                       # the old mount point is gone
    monkeypatch.setattr(folders, "_mounted_shares", lambda: [("nas.local", "music", str(mnt))])
    d = collection.describe(con, 1)
    assert not d["connected"] and d["message"] == collection.MESSAGES["not_connected"]
    assert d["suggestion"] == str(mnt / "lib")
    assert collection.check(con, 1, d["suggestion"])["result"]["code"] == "accepted"


# ---- the app ----------------------------------------------------------------------------------------------
@pytest.fixture
def api(applied, tmp_path):  # noqa: F811
    from music_steward.server import App, make_handler
    lib, dest, run = applied
    app = App(str(tmp_path / "app.sqlite"), [])                              # no --root: the known ones are shown
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ck = login(app, port_ref["port"])
    port = port_ref["port"]

    def get(path):
        return json.loads(req(port, path, cookie=ck)[2])

    def post(path, body):
        return json.loads(req(port, path, cookie=ck, method="POST", headers={"X-MLT": "1"}, body=json.dumps(body))[2])

    def wait():
        for _ in range(300):
            if not app.scan_state.get("running"):
                return app.scan_state["result"]
            time.sleep(0.05)
        raise AssertionError("job did not finish")
    yield app, lib, dest, run, get, post, wait
    httpd.shutdown()


def test_the_app_lists_known_folders_and_root_adds(applied, tmp_path):  # noqa: F811
    from music_steward.server import App
    lib, dest, run = applied
    extra = tmp_path / "extra"
    extra.mkdir()
    app = App(str(tmp_path / "app.sqlite"), [str(extra)])
    assert app.roots == [str(lib), str(extra)]                               # adds, never hides


def test_api_change_path_and_not_connected(api, tmp_path):
    app, lib, dest, run, get, post, wait = api
    s = get("/api/settings")
    assert [(c["id"], c["path"], c["connected"]) for c in s["collections"]] == [(1, str(lib), True)]
    new = moved(lib, tmp_path)
    shutil.move(str(lib), str(tmp_path / "unmounted"))                       # the share went away
    st = get("/api/state")
    assert st["roots"][0]["available"] is False
    r = post("/api/scan", {"root": str(lib)})
    assert r["error"] == collection.MESSAGES["not_connected"]
    r = post("/api/retire", {"id": run, "ack": True})                        # every job that reads the folder
    assert r["error"] == collection.MESSAGES["not_connected"]
    c = get(f"/api/collection_check?root_id=1&path={new}")
    assert c["ok"] and c["matched"] == 4
    assert get("/api/settings")["collections"][0]["retire_allowed"] is True
    r = post("/api/collection_path", {"root_id": 1, "path": str(new)})
    assert r["changed"] and r["done"]["code"] == "done"
    assert app.roots == [str(new)] and get("/api/state")["roots"][0]["available"] is True
    assert get("/api/settings")["collections"][0]["retire_allowed"] is False
    assert post("/api/scan", {"root": str(new)}) == {"started": True}
    assert wait()["n_new"] == 0


def test_api_change_path_is_refused_while_a_job_runs(api, tmp_path):
    app, lib, dest, run, get, post, wait = api
    new = moved(lib, tmp_path)
    app.scan_state = {"running": True, "kind": "scan"}
    r = post("/api/collection_path", {"root_id": 1, "path": str(new)})
    assert "Wait until the running job has finished" in r["error"]
    app.scan_state = {"running": False}
    assert post("/api/collection_path", {"root_id": 1, "path": str(new)})["changed"]


def test_command_line_set_path(applied, tmp_path, capsys):  # noqa: F811
    lib, dest, run = applied
    db = str(tmp_path / "app.sqlite")
    new = moved(lib, tmp_path)
    assert cli.main(["--db", db, "collection", "set-path", str(lib), str(tmp_path / "nowhere"), "--yes"]) == 1
    assert cli.main(["--db", db, "collection", "set-path", str(lib), str(new), "--yes"]) == 0
    assert "Path changed." in capsys.readouterr().out
    cli.main(["--db", db, "collection", "list"])
    assert f"connected      {new}" in capsys.readouterr().out


def test_paths_in_the_collection_screens_are_data():
    """Paths the user typed or the app found are never run through the interface translator."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    body = js[js.index("function renderConn("):js.index("// ---------------------------------------------------------------- settings")]
    for needle in ("root.path)", "root.suggestion)", "oldPath)", "c.path)", "res.detail)", "b.detail)"):
        lines = [ln for ln in body.splitlines() if needle in ln and "h(" in ln]
        assert lines and all("data-no-translate" in ln for ln in lines), needle
