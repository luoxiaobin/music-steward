"""Security guards of the local web server."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from music_steward.scanner import scan
from music_steward.server import App, make_handler

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def server(library, tmp_path):
    dbp = tmp_path / "srv.sqlite"
    from music_steward import db
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


def test_decisions_are_validated_and_saved(server):
    app, port = server
    ck = login(app, port)
    from music_steward import db as dbm
    c = dbm.connect(app.db_path)
    c.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) "
              "VALUES(1,'Clean Album','Album','album','album:Clean Album','[1]','done')")
    c.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) "
              "VALUES(1,'Other','Other','album','album:Other','[2]','done')")
    c.execute("INSERT INTO candidate(unit_id, rank, album, distance) VALUES(1, 0, 'Album', 0.1)")
    c.execute("INSERT INTO candidate(unit_id, rank, album, distance) VALUES(2, 0, 'Other', 0.1)")
    c.commit()
    post = lambda body: json.loads(req(port, "/api/decision", cookie=ck, method="POST",
                                       body=json.dumps(body), headers={"X-MLT": "1"})[2])
    assert "error" in post({"unit_id": 1, "action": "accept", "candidate_id": 2})   # other album's candidate
    assert post({"unit_id": 1, "action": "accept", "candidate_id": 1}) == {"ok": True}
    assert c.execute("SELECT action, candidate_id FROM decision WHERE unit_id=1").fetchone()[:] == ("accept", 1)
    assert post({"unit_id": 1, "action": "skip"}) == {"ok": True}
    assert post({"unit_id": 1, "action": "clear"}) == {"ok": True}
    assert c.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
    assert "error" in post({"unit_id": 1, "action": "delete-everything"})


def test_plan_api_refuses_destination_inside_root(server, library):
    app, port = server
    ck = login(app, port)
    r = json.loads(req(port, "/api/plan", cookie=ck, method="POST", headers={"X-MLT": "1"},
                       body=json.dumps({"root": str(library), "path": "", "dest": str(library / "out")}))[2])
    assert "overlaps the source root" in r["error"]
    r = json.loads(req(port, "/api/plan", cookie=ck, method="POST", headers={"X-MLT": "1"},
                       body=json.dumps({"root": str(library), "path": "", "dest": ""}))[2])
    assert "destination" in r["error"]


def test_edit_requires_accept_and_validates(server):
    app, port = server
    ck = login(app, port)
    from music_steward import db as dbm
    c = dbm.connect(app.db_path)
    c.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) "
              "VALUES(1,'A','A','album','album:A','[1,2]','done')")
    c.execute("INSERT INTO candidate(unit_id, rank, album, artist, year, distance) VALUES(1,0,'A','X',2000,0.1)")
    c.commit()
    post = lambda url, body: json.loads(req(port, url, cookie=ck, method="POST", body=json.dumps(body),
                                            headers={"X-MLT": "1"})[2])
    assert "accept a release first" in post("/api/edit", {"unit_id": 1, "overrides": {"album": "B"}})["error"]
    post("/api/decision", {"unit_id": 1, "action": "accept", "candidate_id": 1})
    assert "4-digit" in post("/api/edit", {"unit_id": 1, "overrides": {"year": "19x"}})["error"]
    r = post("/api/edit", {"unit_id": 1, "overrides": {"album": " B ", "year": "1999", "titles": {"2": "T", "99": "no"}}})
    assert r["overrides"] == {"album": "B", "year": 1999, "titles": {"2": "T"}}   # file 99 not in this album
    post("/api/decision", {"unit_id": 1, "action": "accept", "candidate_id": 1})   # re-accept same: edits kept
    assert json.loads(c.execute("SELECT overrides_json FROM decision").fetchone()[0])["album"] == "B"
    assert "MusicBrainz" in post("/api/search", {"unit_id": 1, "link": "https://example.com"})["error"]


def test_bulk_accept_only_ready_or_likely_and_undecided(server):
    app, port = server
    ck = login(app, port)
    from music_steward import db as dbm
    c = dbm.connect(app.db_path)
    for i, (d, fp) in enumerate([(0.10, 1), (0.12, 0), (0.40, 0)], 1):   # ready, likely, needs review
        c.execute("INSERT INTO unit(root_id, folder, label, kind, key, file_ids, status) "
                  "VALUES(1,'F',?,'album',?, '[1]','done')", (f"A{i}", f"album:F{i}"))
        c.execute("INSERT INTO candidate(unit_id, rank, album, distance, fingerprint_agrees, n_mapped, extra_tracks_json) "
                  "VALUES(?,0,?,?,?,1,'[]')", (i, f"A{i}", d, fp))
    c.commit()
    post = lambda body: json.loads(req(port, "/api/bulk_accept", cookie=ck, method="POST", body=json.dumps(body),
                                       headers={"X-MLT": "1"})[2])
    assert "only for Ready and Likely" in post({"root": app.roots[0], "path": "", "confidences": ["needs_review"]})["error"]
    assert post({"root": app.roots[0], "path": "", "confidences": ["ready", "likely"]}) == {"accepted": 2}
    assert post({"root": app.roots[0], "path": "", "confidences": ["ready", "likely"]}) == {"accepted": 0}  # idempotent
    assert {r[0] for r in c.execute("SELECT unit_id FROM decision")} == {1, 2}
