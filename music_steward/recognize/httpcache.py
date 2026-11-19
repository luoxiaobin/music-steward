"""On-disk cache of the web lookups made while matching (MusicBrainz, Deezer, Discogs, AcoustID).

Re-matching an album (resume after an interruption, a re-run, a rule change) then costs no
network requests and none of the services' rate-limit waits. Only successful GET answers and
AcoustID lookups are kept, for MLT_CACHE_DAYS days (default 30). The cache file can be deleted
at any time (`music-steward cache --clear`).

Secrets: entries are keyed by a SHA-256 of method + URL + body, so API keys and tokens are
never stored in the clear; the URL kept for inspection has key/token parameters removed.

Throttling (network requests only): Deezer <= ~6/s (limit 50 per 5 s) and Discogs <= 1/s
(limit 60/min with a token). MusicBrainz (1/s) and AcoustID (3/s) are throttled by beets and
pyacoustid themselves; a cache hit returns before any of those waits.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

THROTTLE = {"api.deezer.com": 0.15, "api.discogs.com": 1.0}
CACHEABLE_POST = ("api.acoustid.org/v2/lookup",)
_SECRET = re.compile(r"(?i)([?&](?:token|key|client|secret|apikey)=)[^&]*")


def default_path() -> Path:
    if os.environ.get("MLT_HTTP_CACHE"):
        return Path(os.environ["MLT_HTTP_CACHE"])
    from ..db import APP_DIR
    return APP_DIR / "http-cache.sqlite"


def ttl_seconds() -> float:
    return float(os.environ.get("MLT_CACHE_DAYS", "30")) * 86400


class Cache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or default_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS http(key TEXT PRIMARY KEY, url TEXT, status INTEGER, "
                         "headers TEXT, body BLOB, stored REAL)")
        self.con.execute("CREATE TABLE IF NOT EXISTS fp(key TEXT PRIMARY KEY, duration REAL, fp BLOB, stored REAL)")
        self.lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str):
        with self.lock:
            return self.con.execute("SELECT status, headers, body, url FROM http WHERE key=? AND stored>?",
                                    (key, time.time() - ttl_seconds())).fetchone()

    def put(self, key: str, url: str, status: int, headers: dict, body: bytes):
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO http VALUES(?,?,?,?,?,?)",
                             (key, _SECRET.sub(r"\1…", url), status, json.dumps(headers), body, time.time()))
            self.con.commit()

    # audio fingerprints: keyed by file path + size + mtime (+ slice for cue albums); no expiry,
    # a changed file gets a new key
    def get_fp(self, key: str):
        with self.lock:
            r = self.con.execute("SELECT duration, fp FROM fp WHERE key=?", (key,)).fetchone()
        return (r[0], bytes(r[1])) if r else None

    def put_fp(self, key: str, duration, fp: bytes):
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO fp VALUES(?,?,?,?)", (key, duration, fp, time.time()))
            self.con.commit()

    def stats(self) -> dict:
        n, size, oldest = self.con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)),0), MIN(stored) FROM http").fetchone()
        hosts = dict(self.con.execute("SELECT substr(url, instr(url,'//')+2, instr(substr(url, instr(url,'//')+2),'/')-1) h, "
                                      "COUNT(*) FROM http GROUP BY h ORDER BY 2 DESC"))
        fps = self.con.execute("SELECT COUNT(*) FROM fp").fetchone()[0]
        return {"path": str(self.path), "entries": n, "bytes": size, "oldest": oldest, "by_host": hosts,
                "fingerprints": fps}

    def clear(self) -> int:
        with self.lock:
            n = self.con.execute("DELETE FROM http").rowcount + self.con.execute("DELETE FROM fp").rowcount
            self.con.commit()
        self.con.execute("VACUUM")
        return n


def request_key(method: str, url: str, body) -> str:
    if isinstance(body, str):
        body = body.encode()
    return hashlib.sha256(method.upper().encode() + b"\0" + url.encode() + b"\0" + (body or b"")).hexdigest()


def cacheable(method: str, url: str) -> bool:
    if method.upper() == "GET":
        return True
    return method.upper() == "POST" and any(p in url for p in CACHEABLE_POST)


_last: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(url: str):
    host = urlsplit(url).hostname or ""
    gap = THROTTLE.get(host)
    if not gap:
        return
    with _throttle_lock:
        wait = _last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()


def _response(request, status, headers, body):
    import requests
    from requests.structures import CaseInsensitiveDict
    r = requests.Response()
    r.status_code, r.reason = status, "OK (cached)"
    r.headers = CaseInsensitiveDict(json.loads(headers))
    r._content, r._content_consumed = body, True
    r.url, r.request, r.encoding = request.url, request, None
    import datetime
    r.elapsed = datetime.timedelta(0)
    r.from_cache = True
    return r


_installed = False
_cache: Cache | None = None
_originals: list = []


def install(path: Path | str | None = None) -> Cache | None:
    """Route every `requests` call in this process through the cache. Idempotent.
    MLT_NO_CACHE=1 turns caching off (throttling stays)."""
    global _installed, _cache
    if _installed:
        return _cache
    import requests
    cache = None if os.environ.get("MLT_NO_CACHE") == "1" else Cache(path)

    def wrap(orig):
        def send(self, request, **kw):
            if getattr(request, "_mlt_inner", False):     # already handled by an outer layer
                return orig(self, request, **kw)
            use = cache is not None and cacheable(request.method, request.url) and not kw.get("stream")
            key = request_key(request.method, request.url, request.body) if use else None
            if use:
                hit = cache.get(key)
                if hit:
                    cache.hits += 1
                    return _response(request, hit[0], hit[1], hit[2])
                cache.misses += 1
            _throttle(request.url)
            request._mlt_inner = True
            try:
                r = orig(self, request, **kw)
            finally:
                request._mlt_inner = False
            if use and r.status_code == 200:
                headers = {k: v for k, v in r.headers.items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding",
                                                "set-cookie")}
                cache.put(key, request.url, r.status_code, headers, r.content)
            return r
        send._mlt_wrapped = True
        return send

    targets = [requests.Session]
    try:
        from requests_ratelimiter import LimiterMixin   # MusicBrainz's 1/s limiter sits before Session.send
        targets.insert(0, LimiterMixin)
    except ImportError:
        pass
    for cls in targets:
        if "send" in cls.__dict__ and not getattr(cls.__dict__["send"], "_mlt_wrapped", False):
            _originals.append((cls, cls.__dict__["send"]))
            cls.send = wrap(cls.__dict__["send"])
    _installed, _cache = True, cache
    if cache is not None:
        _install_fp_cache()
    return cache


fp_keys: dict[str, str] = {}     # path being fingerprinted -> content key


def file_key(path: str, extra: str = "") -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{path}\0{st.st_size}\0{st.st_mtime_ns}\0{extra}"


def cached_fingerprint(key: str | None):
    return _cache.get_fp(key) if key and _cache is not None else None


def _install_fp_cache():
    import acoustid
    if getattr(acoustid.fingerprint_file, "_mlt_wrapped", False):
        return
    orig = acoustid.fingerprint_file

    def fingerprint_file(path, *a, **kw):
        key = fp_keys.get(os.fsdecode(path))
        hit = cached_fingerprint(key)
        if hit:
            return hit
        res = orig(path, *a, **kw)
        if key and _cache is not None:
            _cache.put_fp(key, res[0], res[1])
        return res
    fingerprint_file._mlt_wrapped = True
    _originals.append((acoustid, orig, "fingerprint_file"))
    acoustid.fingerprint_file = fingerprint_file


def uninstall():
    """Restore the original `requests` methods (tests)."""
    global _installed, _cache
    while _originals:
        cls, fn, *name = _originals.pop()
        setattr(cls, name[0] if name else "send", fn)
    fp_keys.clear()
    if _cache is not None:
        _cache.con.close()
    _installed, _cache = False, None
    _last.clear()
