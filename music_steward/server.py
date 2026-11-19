"""Local web UI server (stdlib only).

Security model (from the architecture assessment):
  * binds 127.0.0.1 on a random free port
  * a one-time token in the launch URL is exchanged for an HttpOnly, SameSite=Strict
    cookie; every API call needs that cookie
  * requests whose Host header is not 127.0.0.1/localhost:<port> are rejected
    (blocks DNS rebinding)
  * only files that are in the inventory AND inside a configured root are ever served
  * read-only on the collection; the only thing it can start is a (read-only) scan
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import audit, db
from .paths import nfc
from .scanner import RootUnavailable, scan

WEB = Path(__file__).parent / "web"
AUDIO_MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
              ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg"}
BROWSER_PLAYABLE = set(AUDIO_MIME)


class App:
    def __init__(self, db_path: str, roots: list[str], dest: str | None = None):
        self.dest = os.path.abspath(os.path.expanduser(dest)) if dest else None
        self.db_path = db_path
        self.roots = [os.path.abspath(r) for r in roots]
        self.token = secrets.token_urlsafe(24)
        self.session = secrets.token_urlsafe(32)
        self.scan_state = {"running": False}
        self._lock = threading.Lock()
        self._summary_cache: dict = {}
        con = db.connect(db_path)
        con.close()

    def con(self):
        return db.connect(self.db_path)

    # ---- data -------------------------------------------------------------
    def roots_info(self, con):
        out = []
        for r in self.roots:
            row = con.execute("SELECT id FROM root WHERE path=?", (nfc(r),)).fetchone()
            last = None
            if row:
                s = con.execute("SELECT * FROM scan WHERE root_id=? ORDER BY id DESC LIMIT 1",
                                (row["id"],)).fetchone()
                last = dict(s) if s else None
            out.append({"path": r, "root_id": row["id"] if row else None,
                        "available": os.path.isdir(r), "last_scan": last})
        return out

    def summary(self, con, root_id):
        key = (root_id, con.execute("SELECT max(id) FROM scan").fetchone()[0],
               con.execute("SELECT count(*) FROM file WHERE file_sha256 IS NOT NULL").fetchone()[0])
        if key not in self._summary_cache:
            self._summary_cache = {key: audit.summary(con, root_id)}
        return self._summary_cache[key]

    def folders(self, con, root_id, path):
        """Immediate sub-folders of `path` with recursive audio counts."""
        prefix = f"{path}/" if path else ""
        counts: dict[str, int] = {}
        q = ("SELECT relpath FROM file WHERE root_id=? AND removed_at IS NULL AND kind='audio'"
             + (" AND relpath >= ? AND relpath < ?" if prefix else ""))
        args = (root_id, prefix, prefix + "￿") if prefix else (root_id,)
        direct = 0
        for (rel,) in con.execute(q, args):
            rest = rel[len(prefix):]
            if "/" in rest:
                d = rest.split("/", 1)[0]
                counts[d] = counts.get(d, 0) + 1
            else:
                direct += 1
        hashed = con.execute(
            "SELECT count(*) FROM file WHERE root_id=? AND removed_at IS NULL AND kind='audio' "
            "AND file_sha256 IS NOT NULL" + (" AND relpath >= ? AND relpath < ?" if prefix else ""),
            args).fetchone()[0]
        return {"path": path, "direct_audio": direct, "audio": direct + sum(counts.values()),
                "hashed": hashed,
                "folders": [{"name": k, "path": prefix + k, "audio": v}
                            for k, v in sorted(counts.items())]}

    def files(self, con, root_id, path, flt, limit=2000):
        prefix = f"{path}/" if path else ""
        rows = con.execute(
            "SELECT id, relpath, kind, ext, size, codec, container, duration, bit_rate, "
            "bit_rate_estimated, sample_rate, bits_per_sample, probe_status, tags_json, "
            "tag_issues_json, file_sha256, audio_hash FROM file WHERE root_id=? AND removed_at IS NULL "
            "AND kind='audio'" + (" AND relpath >= ? AND relpath < ?" if prefix else "") +
            " ORDER BY relpath", (root_id, prefix, prefix + "￿") if prefix else (root_id,)).fetchall()
        if flt:
            keep = {r["relpath"] for r in self._filter_rows(con, flt, root_id)}
            rows = [r for r in rows if r["relpath"] in keep]
        total = len(rows)
        out = []
        for r in rows[:limit]:
            t = json.loads(r["tags_json"] or "{}")
            issues = json.loads(r["tag_issues_json"] or "[]")
            fixed = {i["field"]: i.get("proposal") for i in issues if i.get("proposal")}
            out.append({
                "id": r["id"], "relpath": r["relpath"], "ext": r["ext"], "size": r["size"],
                "codec": r["codec"], "container": r["container"], "duration": r["duration"],
                "bit_rate": r["bit_rate"], "bit_rate_estimated": bool(r["bit_rate_estimated"]),
                "sample_rate": r["sample_rate"], "bits": r["bits_per_sample"],
                "unreadable": r["probe_status"] == "error",
                "title": t.get("title"), "artist": t.get("artist"), "album": t.get("album"),
                "albumartist": t.get("albumartist"), "track": t.get("track"), "date": t.get("date"),
                "fixed": fixed, "garbled": bool(issues),
                "hashed": bool(r["file_sha256"]),
            })
        return {"total": total, "shown": len(out), "files": out}

    def _filter_rows(self, con, flt, root_id):
        rows = audit.run(con, flt, root_id)
        if flt in ("exact_duplicates", "same_audio"):
            return [{"relpath": f} for g in rows for f in g["files"]]
        return rows

    def file_detail(self, con, fid):
        r = con.execute("SELECT f.*, r.path AS root FROM file f JOIN root r ON r.id=f.root_id "
                        "WHERE f.id=?", (fid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json") or "{}")
        d["issues"] = json.loads(d.pop("tag_issues_json") or "[]")
        folder = os.path.dirname(d["relpath"])
        stem = os.path.splitext(os.path.basename(d["relpath"]))[0]
        prefix = f"{folder}/" if folder else ""
        side = con.execute(
            "SELECT relpath, kind FROM file WHERE root_id=? AND removed_at IS NULL AND kind IN "
            "('cue','lrc','image') AND relpath >= ? AND relpath < ?",
            (d["root_id"], prefix, prefix + "￿")).fetchall()
        d["side_files"] = [{"relpath": s["relpath"], "kind": s["kind"],
                            "matches_track": os.path.splitext(os.path.basename(s["relpath"]))[0] == stem}
                           for s in side if "/" not in s["relpath"][len(prefix):]]
        d["same_audio"] = [x["relpath"] for x in con.execute(
            "SELECT relpath FROM file WHERE audio_hash=? AND id<>? AND removed_at IS NULL",
            (d["audio_hash"], fid))] if d["audio_hash"] else []
        d["playable"] = d["ext"] in BROWSER_PLAYABLE and d["probe_status"] == "ok"
        d.pop("root")
        return d

    def audio_path(self, con, fid) -> str | None:
        r = con.execute("SELECT f.fs_relpath, f.ext, f.kind, r.path AS root FROM file f JOIN root r "
                        "ON r.id=f.root_id WHERE f.id=? AND f.removed_at IS NULL", (fid,)).fetchone()
        if not r or r["kind"] != "audio" or r["ext"] not in BROWSER_PLAYABLE:
            return None
        p = os.path.realpath(os.path.join(r["root"], r["fs_relpath"]))
        if not any(p.startswith(os.path.realpath(root) + os.sep) for root in self.roots):
            return None
        return p

    def start_job(self, kind: str, root: str, path: str = "") -> dict:
        """Background scan or hash of a root or one of its sub-folders. One job at a time."""
        if kind in ("apply", "undo", "resume", "recognize_unit"):
            root = root or self.roots[0]
            if not str(path).isdigit():
                return {"error": "bad id"}
        root = os.path.abspath(root)
        if root not in self.roots:
            return {"error": "not a configured root"}
        if kind in ("apply", "undo", "resume", "recognize_unit"):
            pass
        elif path and (".." in path.split("/") or path.startswith("/")):
            return {"error": "bad path"}
        target = os.path.join(root, str(path)) if path else root
        if kind not in ("apply", "undo", "resume", "recognize_unit") and not os.path.realpath(target).startswith(os.path.realpath(root)):
            return {"error": "bad path"}
        with self._lock:
            if self.scan_state.get("running"):
                return {"error": f"a {self.scan_state.get('kind')} is already running"}
            self.scan_state = {"running": True, "kind": kind, "root": root, "path": path,
                               "done": 0, "total": None, "started": time.time()}

        def prog(done, total):
            self.scan_state.update(done=done, total=total)

        def work():
            con = self.con()
            try:
                if kind == "scan":
                    r = scan(con, target, progress=prog)
                    res = {"status": r.status, **r.counts, "abort_reason": r.abort_reason}
                elif kind in ("apply", "undo", "resume"):
                    from .apply import runner
                    n = int(path)
                    res = {"status": "complete", **(runner.apply(con, n, progress=prog) if kind == "apply"
                                                    else runner.undo(con, n) if kind == "undo"
                                                    else runner.resume(con, n))}
                    if res.get("error"):
                        res["status"], res["abort_reason"] = "error", res["error"]
                elif kind == "recognize_unit":
                    from .recognize import queue
                    res = {"status": "complete", **queue.run(self.db_path, [int(path)], progress=prog)}
                elif kind == "recognize":
                    from .recognize import queue, units
                    rid = con.execute("SELECT id FROM root WHERE path=?", (nfc(root),)).fetchone()["id"]
                    sub = nfc(path)
                    q = units.enqueue(con, rid, units.plan_units(con, rid, sub))
                    ids = queue.pending(con, rid, sub)
                    res = {"status": "complete", **q, **queue.run(self.db_path, ids, progress=prog)}
                else:
                    from . import hashpass
                    rid = con.execute("SELECT id FROM root WHERE path=?", (nfc(root),)).fetchone()["id"]
                    res = {"status": "complete", **hashpass.run(con, rid, subpath=nfc(path), progress=prog)}
                self.scan_state.update(running=False, result=res, finished=time.time())
            except RootUnavailable as e:
                self.scan_state.update(running=False, result={"status": "aborted", "abort_reason": str(e)})
            except Exception as e:  # pragma: no cover
                self.scan_state.update(running=False, result={"status": "error", "abort_reason": repr(e)})
            finally:
                con.close()
        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def make_plan(self, con, body) -> dict:
        from .apply import plan
        root = os.path.abspath(body.get("root", ""))
        if root not in self.roots:
            return {"error": "not a configured root"}
        dest = (body.get("dest") or self.dest or "").strip()
        if not dest:
            return {"error": "choose a destination folder first"}
        row = con.execute("SELECT id FROM root WHERE path=?", (nfc(root),)).fetchone()
        try:
            pid = plan.build(con, os.path.expanduser(dest), row["id"], nfc(body.get("path", "")))
        except plan.PlanError as e:
            return {"error": str(e)}
        return {"plan_id": pid}

    def plan_view(self, con, plan_id) -> dict | None:
        from .apply import plan
        if not con.execute("SELECT 1 FROM plan WHERE id=?", (plan_id,)).fetchone():
            return None
        p = plan.load(con, plan_id)
        for it in p["items"]:
            f = con.execute("SELECT tags_json, tag_issues_json, relpath FROM file WHERE id=?", (it["file_id"],)).fetchone()
            it["current_tags"] = json.loads(f["tags_json"] or "{}") if f else {}
            it["src_rel"] = f["relpath"] if f else it["src"]
        probe_dir = Path(p["dest_root"])
        while not probe_dir.exists() and probe_dir != probe_dir.parent:
            probe_dir = probe_dir.parent
        try:
            import shutil
            p["dest_free"] = shutil.disk_usage(probe_dir).free
        except OSError:
            p["dest_free"] = None
        for r in p["runs"]:
            r["summary"] = json.loads(r.pop("summary_json") or "{}")
        return p

    def search_again(self, con, body) -> dict:
        from .recognize import search as srch
        uid = _int(body.get("unit_id"))
        u = con.execute("SELECT * FROM unit WHERE id=?", (uid,)).fetchone()
        if not u:
            return {"error": "no such album"}
        try:
            s = srch.parse(u["kind"], body.get("artist", ""), body.get("name", ""), body.get("link", ""))
        except srch.SearchError as e:
            return {"error": str(e)}
        con.execute("UPDATE unit SET search_json=?, status='queued' WHERE id=?", (json.dumps(s, ensure_ascii=False), uid))
        con.commit()
        root = con.execute("SELECT path FROM root WHERE id=?", (u["root_id"],)).fetchone()["path"]
        return self.start_job("recognize_unit", root, str(uid))

    def edit_fields(self, con, body) -> dict:
        uid = _int(body.get("unit_id"))
        d = con.execute("SELECT * FROM decision WHERE unit_id=? AND action='accept'", (uid,)).fetchone()
        if not d:
            return {"error": "accept a release first, then edit its fields"}
        ov = body.get("overrides") or {}
        clean = {}
        for k in ("album", "albumartist"):
            if isinstance(ov.get(k), str) and ov[k].strip():
                clean[k] = ov[k].strip()
        if str(ov.get("year", "")).strip():
            if not str(ov["year"]).strip().isdigit() or not 1000 <= int(ov["year"]) <= 2999:
                return {"error": "year must be a 4-digit number"}
            clean["year"] = int(ov["year"])
        ids = set(json.loads(con.execute("SELECT file_ids FROM unit WHERE id=?", (uid,)).fetchone()[0]))
        titles = {str(int(k)): v.strip() for k, v in (ov.get("titles") or {}).items()
                  if str(k).isdigit() and int(k) in ids and isinstance(v, str) and v.strip()}
        if titles:
            clean["titles"] = titles
        con.execute("UPDATE decision SET overrides_json=? WHERE unit_id=?",
                    (json.dumps(clean, ensure_ascii=False) if clean else None, uid))
        con.commit()
        return {"ok": True, "overrides": clean}

    def decide(self, con, body) -> dict:
        """Save a review decision immediately (Accept a candidate, or Skip)."""
        uid, action = _int(body.get("unit_id")), body.get("action")
        if action not in ("accept", "skip", "clear") or not uid:
            return {"error": "bad decision"}
        if action == "clear":
            con.execute("DELETE FROM decision WHERE unit_id=?", (uid,))
        else:
            cid = _int(body.get("candidate_id")) if action == "accept" else None
            if action == "accept" and not con.execute(
                    "SELECT 1 FROM candidate WHERE id=? AND unit_id=?", (cid, uid)).fetchone():
                return {"error": "candidate does not belong to this album"}
            from .recognize.results import release_key
            key = release_key(con, cid) if action == "accept" else None
            prev = con.execute("SELECT candidate_id, overrides_json FROM decision WHERE unit_id=?", (uid,)).fetchone()
            keep = prev["overrides_json"] if prev and action == "accept" and prev["candidate_id"] == cid else None
            con.execute("INSERT OR REPLACE INTO decision(unit_id, action, candidate_id, decided_at, release_key, "
                        "overrides_json) VALUES(?,?,?,?,?,?)", (uid, action, cid, time.time(), key, keep))
            con.execute("UPDATE unit SET notice=NULL WHERE id=?", (uid,))
        con.commit()
        return {"ok": True}

    def start_scan(self, root: str, path: str = "") -> dict:  # kept for callers/tests
        return self.start_job("scan", root, path)


def make_handler(app: App, port_ref: dict):
    class H(BaseHTTPRequestHandler):
        server_version = "MusicSteward"

        def log_message(self, fmt, *args):  # quiet
            pass

        # ---- guards ---------------------------------------------------
        def _host_ok(self):
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{port_ref['port']}", f"localhost:{port_ref['port']}")

        def _authed(self):
            cookie = self.headers.get("Cookie", "")
            return re.search(r"(?:^|;\s*)mlt_session=" + re.escape(app.session) + r"(?:;|$)", cookie)

        def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode())

        # ---- routes ---------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, b"bad host")
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/" and q.get("token") is not None:
                if not secrets.compare_digest(q["token"], app.token):
                    return self._send(HTTPStatus.FORBIDDEN, b"bad token")
                return self._send(HTTPStatus.SEE_OTHER, b"", headers={
                    "Location": "/",
                    "Set-Cookie": f"mlt_session={app.session}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._authed():
                return self._send(HTTPStatus.UNAUTHORIZED,
                                  b"Open the URL printed by `music-steward serve`.", "text/plain")
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            con = app.con()
            try:
                if u.path == "/api/state":
                    roots = app.roots_info(con)
                    rid = _int(q.get("root_id")) or next((r["root_id"] for r in roots if r["root_id"]), None)
                    return self._json({"roots": roots, "root_id": rid, "dest": app.dest,
                                       "summary": app.summary(con, rid) if rid else None,
                                       "scan": app.scan_state})
                if u.path == "/api/folders":
                    return self._json(app.folders(con, _int(q["root_id"]), q.get("path", "")))
                if u.path == "/api/files":
                    flt = q.get("filter") or None
                    if flt and flt not in audit.FILTERS:
                        return self._json({"error": "unknown filter"}, 400)
                    return self._json(app.files(con, _int(q["root_id"]), q.get("path", ""), flt))
                if u.path == "/api/units":
                    from .recognize import results
                    return self._json(results.units(con, _int(q["root_id"]), q.get("path", "")))
                if u.path == "/api/plans":
                    return self._json([dict(r) for r in con.execute(
                        "SELECT id, created_at, dest_root, scope, summary_json FROM plan ORDER BY id DESC LIMIT 20")])
                m = re.fullmatch(r"/api/plan/(\d+)", u.path)
                if m:
                    d = app.plan_view(con, int(m.group(1)))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                m = re.fullmatch(r"/api/unit/(\d+)", u.path)
                if m:
                    from .recognize import results
                    d = results.unit_detail(con, int(m.group(1)))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                m = re.fullmatch(r"/api/file/(\d+)", u.path)
                if m:
                    d = app.file_detail(con, int(m.group(1)))
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                m = re.fullmatch(r"/api/audio/(\d+)", u.path)
                if m:
                    p = app.audio_path(con, int(m.group(1)))
                    if not p:
                        return self._send(HTTPStatus.NOT_FOUND, b"")
                    return self._range(p)
                return self._json({"error": "no such endpoint"}, 404)
            finally:
                con.close()

        def do_POST(self):
            if not self._host_ok() or not self._authed():
                return self._send(HTTPStatus.FORBIDDEN, b"")
            if self.headers.get("X-MLT") != "1":  # simple CSRF guard on top of SameSite
                return self._send(HTTPStatus.FORBIDDEN, b"")
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/api/plan":
                con = app.con()
                try:
                    return self._json(app.make_plan(con, body))
                finally:
                    con.close()
            if u.path in ("/api/apply", "/api/undo", "/api/resume"):
                kind = u.path.rsplit("/", 1)[1]
                return self._json(app.start_job(kind, "", str(body.get("id", ""))))
            if u.path in ("/api/search", "/api/edit"):
                con = app.con()
                try:
                    return self._json(app.search_again(con, body) if u.path == "/api/search"
                                      else app.edit_fields(con, body))
                finally:
                    con.close()
            if u.path == "/api/decision":
                con = app.con()
                try:
                    return self._json(app.decide(con, body))
                finally:
                    con.close()
            if u.path in ("/api/scan", "/api/hash", "/api/recognize"):
                kind = u.path.rsplit("/", 1)[1]
                return self._json(app.start_job(kind, body.get("root", ""), body.get("path", "")))
            return self._json({"error": "no such endpoint"}, 404)

        def _static(self, name):
            p = (WEB / name).resolve()
            if not str(p).startswith(str(WEB.resolve())) or not p.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, p.read_bytes(), ctype, headers={
                "Content-Security-Policy": "default-src 'self'; media-src 'self'; img-src 'self' data:; "
                                           "style-src 'self' 'unsafe-inline'; "
                                           "script-src 'self'; frame-ancestors 'none'"})

        def _range(self, path):
            size = os.path.getsize(path)
            ctype = AUDIO_MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            code = 200
            if rng:
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                if not m:
                    return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"")
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1, start + 8 * 1024 * 1024 - 1)
                if start > end:
                    return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"")
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(path, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 16, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

    return H


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def serve(db_path: str, roots: list[str], port: int = 0, open_browser: bool = True, dest: str | None = None):
    app = App(db_path, roots, dest)
    port_ref = {"port": port}
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref["port"] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref['port']}/?token={app.token}"
    print(f"Music Steward is running at:\n  {url}\n(Ctrl-C to stop)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return app, httpd
