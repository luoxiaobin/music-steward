"""One window (or `serve`) per database: the owner started the Mac app twice and had two windows on one database."""
import os
import stat
import subprocess
import sys
import threading

import pytest

from music_steward import single


def test_a_second_instance_is_refused_and_brings_the_first_forward(tmp_path):
    db = str(tmp_path / "app.sqlite")
    first = single.acquire(db)
    assert first is not None
    shown = threading.Event()
    first.listen(shown.set)
    assert single.acquire(db) is None                        # the second start
    assert single.ask_to_show(db) and shown.wait(5)
    first.close()
    again = single.acquire(db)                               # free once the first has quit
    assert again is not None
    again.close()


def test_another_database_is_another_app(tmp_path):
    a, b = single.acquire(str(tmp_path / "a.sqlite")), single.acquire(str(tmp_path / "b.sqlite"))
    assert a and b
    a.close(), b.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix socket permissions")
def test_only_this_user_can_ask(tmp_path):
    me = single.acquire(str(tmp_path / "app.sqlite"))
    me.listen(lambda: None)
    sock = single._paths(str(tmp_path / "app.sqlite"))[1]
    assert stat.S_IMODE(os.stat(sock).st_mode) & 0o077 == 0
    me.close()
    assert not sock.exists()


def test_a_deep_folder_still_gets_a_usable_socket(tmp_path):
    deep = tmp_path / ("x" * 60) / ("y" * 60)
    deep.mkdir(parents=True)
    db = str(deep / "app.sqlite")
    assert len(str(single._paths(db)[1])) <= 100
    me = single.acquire(db)
    shown = threading.Event()
    me.listen(shown.set)
    assert single.ask_to_show(db) and shown.wait(5)
    me.close()


def test_a_crash_leaves_no_stale_lock(tmp_path):
    db = str(tmp_path / "app.sqlite")
    code = ("import os, sys; sys.path.insert(0, %r); from music_steward import single; "
            "me = single.acquire(%r); me.listen(lambda: None); os._exit(0)") % (os.getcwd(), db)
    subprocess.run([sys.executable, "-c", code], check=True)  # dies holding the lock, socket file left behind
    me = single.acquire(db)
    assert me is not None
    shown = threading.Event()
    me.listen(shown.set)                                     # the stale socket file is replaced
    assert single.ask_to_show(db) and shown.wait(5)
    me.close()


def test_the_app_and_serve_take_the_lock(tmp_path, capsys):
    from music_steward import desktop, server
    db = str(tmp_path / "app.sqlite")
    held = single.acquire(db)
    desktop.run(db, [])                                      # returns at once, without opening a window
    assert "already open" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already open"):
        server.serve(db, [], open_browser=False)
    held.close()
