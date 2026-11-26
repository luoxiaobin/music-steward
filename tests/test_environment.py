"""The packaged app's environment: tools on PATH, the keys from the login shell (names only, never stored), the
recognition worker started by the app binary itself, the first launch with no collection folder, and the self-test."""
import json
import os
import sys

from music_steward import environment, selftest


def test_fix_path_appends_existing_tool_folders_once(tmp_path, monkeypatch):
    brew = tmp_path / "homebrew" / "bin"
    brew.mkdir(parents=True)
    monkeypatch.setattr(environment, "TOOL_DIRS", (str(brew), str(tmp_path / "missing")))
    env = {"PATH": "/usr/bin:/bin"}
    assert environment.fix_path(env) == [str(brew)]
    assert env["PATH"] == f"/usr/bin:/bin:{brew}"                            # after what was there; only what exists
    assert environment.fix_path(env) == [] and env["PATH"].count(str(brew)) == 1


def test_only_the_named_variables_come_from_the_login_shell(tmp_path):
    shell = tmp_path / "fake-shell"
    shell.write_text("#!/bin/sh\nprintf 'ACOUSTID_API_KEY=abc\\0DISCOGS_TOKEN=t=ok\\0MLT_SCRIPT=keep\\0"
                     "AWS_SECRET=no\\0PATH=/evil\\0'\n")
    shell.chmod(0o755)
    assert environment.login_env(str(shell)) == {"ACOUSTID_API_KEY": "abc", "DISCOGS_TOKEN": "t=ok", "MLT_SCRIPT": "keep"}
    env = {"DISCOGS_TOKEN": "already set"}
    names = environment.import_login_env(env, str(shell))
    assert sorted(names) == ["ACOUSTID_API_KEY", "MLT_SCRIPT"]                 # names, never values
    assert env == {"DISCOGS_TOKEN": "already set", "ACOUSTID_API_KEY": "abc", "MLT_SCRIPT": "keep"}


def test_a_shell_that_hangs_or_is_missing_gives_nothing(tmp_path):
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    assert environment.login_env(str(slow), timeout=0.5) == {}
    assert environment.login_env(str(tmp_path / "no-such-shell")) == {}


def test_the_worker_command_inside_and_outside_the_app(monkeypatch):
    assert environment.worker_cmd(["--db", "x"])[1:4] == ["-I", "-m", "music_steward.recognize.worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert environment.worker_cmd(["--db", "x", "--unit", "3"]) == [sys.executable, "--recognize-worker",
                                                                   "--db", "x", "--unit", "3"]


def test_the_app_entry_dispatches_a_worker(monkeypatch, tmp_path):
    """The packaged app's binary is also the worker: `Music Steward --recognize-worker --db … --unit …`."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "app_main", Path(__file__).resolve().parents[1] / "packaging/macos/app_main.py")
    app_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_main)
    seen = {}
    from music_steward.recognize import worker
    monkeypatch.setattr(worker, "run_unit", lambda db, unit: seen.update(db=db, unit=unit) or 0)
    monkeypatch.setattr(sys, "argv", ["Music Steward", "--recognize-worker", "--db", "/x.sqlite", "--unit", "7"])
    assert app_main.main() == 0 and seen == {"db": "/x.sqlite", "unit": 7}


def test_first_launch_has_no_collection_folder_and_the_page_still_answers(tmp_path):
    from http.server import ThreadingHTTPServer
    import threading
    from music_steward.server import App, make_handler
    from .test_server import login, req
    app = App(str(tmp_path / "fresh.sqlite"), [])
    ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ref))
    ref["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ck = login(app, ref["port"])
        st = json.loads(req(ref["port"], "/api/state", cookie=ck)[2])
        assert st["roots"] == [] and st["root_id"] is None and st["ffmpeg"] in (True, False)
        assert req(ref["port"], "/", cookie=ck)[0] == 200
    finally:
        httpd.shutdown()


def test_the_app_command_opens_with_no_collection_folder(tmp_path, monkeypatch):
    """`music-steward app` used to quit with "no collection yet"; the window now greets you instead."""
    import types
    from music_steward import cli
    opened = {}
    fake = types.ModuleType("webview")
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr("music_steward.desktop.run", lambda db, roots, dest=None, debug=False: opened.update(roots=roots))
    cli.main(["--db", str(tmp_path / "fresh.sqlite"), "app"])
    assert opened == {"roots": []}


def test_the_self_test_passes_on_this_install(capsys):
    assert selftest.run() == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "recognition worker: starts" in out


def test_the_welcome_and_ffmpeg_notices_are_interface_text():
    from pathlib import Path
    from music_steward import i18n
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    table = {r[0] for r in i18n.TABLE}
    for text in ("Welcome.", "FFmpeg is not installed.", "Add a collection folder…"):
        assert f'"{text}' in js and text in table, text


def test_no_secret_is_ever_printed_by_prepare(monkeypatch, tmp_path, capsys):
    shell = tmp_path / "sh"
    shell.write_text("#!/bin/sh\nprintf 'ACOUSTID_API_KEY=very-secret\\0'\n")
    shell.chmod(0o755)
    monkeypatch.setenv("SHELL", str(shell))
    monkeypatch.delenv("ACOUSTID_API_KEY", raising=False)
    r = environment.prepare()
    assert "very-secret" not in json.dumps(r) + capsys.readouterr().out
    assert os.environ["ACOUSTID_API_KEY"] == "very-secret"                   # in this process's memory only
    monkeypatch.delenv("ACOUSTID_API_KEY")
