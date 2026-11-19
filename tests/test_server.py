"""Security guards of the local web server."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from mp3librarytool.scanner import scan
from mp3librarytool.server import App, make_handler

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def server(library, tmp_path):
    dbp = tmp_path / "srv.sqlite"
    from mp3librarytool import db
    c = db.connect(dbp)
    scan(c, library)
    c.close()
    app = App(str(dbp), [str(library)])
    port_ref = {"port": 0}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app, port_ref["port"]
    httpd.shutdown()


def req(port, path, host=None, cookie=None, method="GET", headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def login(app, port):
    st, hd, _ = req(port, f"/?token={app.token}")
    assert st == 303
    return hd["Set-Cookie"].split(";")[0]


def test_api_needs_session(server):
    app, port = server
    assert req(port, "/api/state")[0] == 401
    assert req(port, "/?token=wrong")[0] == 403


def test_token_gives_httponly_strict_cookie(server):
    app, port = server
    st, hd, _ = req(port, f"/?token={app.token}")
    assert "HttpOnly" in hd["Set-Cookie"] and "SameSite=Strict" in hd["Set-Cookie"]


def test_foreign_host_header_rejected(server):
    app, port = server
    ck = login(app, port)
    assert req(port, "/api/state", host="evil.example:80", cookie=ck)[0] == 403
    assert req(port, "/api/state", cookie=ck)[0] == 200


def test_post_needs_custom_header(server):
    app, port = server
    ck = login(app, port)
    body = json.dumps({"root": "/"})
    assert req(port, "/api/scan", cookie=ck, method="POST", body=body)[0] == 403
    st, _, out = req(port, "/api/scan", cookie=ck, method="POST", body=body, headers={"X-MLT": "1"})
    assert st == 200 and "not a configured root" in json.loads(out)["error"]


def test_audio_served_only_for_inventory_files_with_ranges(server):
    app, port = server
    ck = login(app, port)
    files = json.loads(req(port, f"/api/files?root_id=1&path=", cookie=ck)[2])["files"]
    mp3 = next(f for f in files if f["relpath"] == "Clean Album/01 - one.mp3")
    st, hd, body = req(port, f"/api/audio/{mp3['id']}", cookie=ck, headers={"Range": "bytes=0-99"})
    assert st == 206 and len(body) == 100 and hd["Content-Range"].startswith("bytes 0-99/")
    assert req(port, "/api/audio/999999", cookie=ck)[0] == 404


def test_static_cannot_escape_web_dir(server):
    app, port = server
    ck = login(app, port)
    assert req(port, "/static/../server.py", cookie=ck)[0] == 404
    assert req(port, "/static/app.js", cookie=ck)[0] == 200
