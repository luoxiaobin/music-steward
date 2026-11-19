"""Web lookup cache: hits skip the network and the rate-limit waits; secrets are never stored."""
import time

import pytest
import requests
from requests.adapters import BaseAdapter

from music_steward.recognize import httpcache


class FakeNet(BaseAdapter):
    """Stands in for the network: counts requests, answers JSON."""
    def __init__(self, status=200):
        super().__init__()
        self.calls, self.status = [], status

    def send(self, request, **kw):
        self.calls.append((request.method, request.url, request.body))
        r = requests.Response()
        r.status_code, r.url, r.request = self.status, request.url, request
        r._content = b'{"n": %d}' % len(self.calls)
        r.headers["Content-Type"] = "application/json"
        return r

    def close(self):
        pass


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.delenv("MLT_NO_CACHE", raising=False)
    monkeypatch.delenv("MLT_CACHE_DAYS", raising=False)
    c = httpcache.install(tmp_path / "http.sqlite")
    yield c
    httpcache.uninstall()


def session(net):
    s = requests.Session()
    s.mount("http://", net)
    s.mount("https://", net)
    return s


def test_get_is_cached(cache):
    net = FakeNet()
    s = session(net)
    a = s.get("https://musicbrainz.org/ws/2/release?query=x")
    b = s.get("https://musicbrainz.org/ws/2/release?query=x")
    assert a.json() == b.json() == {"n": 1} and len(net.calls) == 1
    assert getattr(b, "from_cache", False) and b.headers["Content-Type"] == "application/json"
    s.get("https://musicbrainz.org/ws/2/release?query=y")
    assert len(net.calls) == 2 and (cache.hits, cache.misses) == (1, 2)


def test_only_successes_and_acoustid_posts_are_cached(cache):
    bad = FakeNet(status=503)
    s = session(bad)
    s.get("https://api.deezer.com/search?q=a")
    s.get("https://api.deezer.com/search?q=a")
    assert len(bad.calls) == 2                      # errors are retried, never cached
    net = FakeNet()
    s = session(net)
    for _ in range(2):
        s.post("http://api.acoustid.org/v2/lookup", data={"client": "SECRETKEY123", "fingerprint": "AQAA"})
        s.post("https://example.org/submit", data={"a": "1"})
    assert [c[1] for c in net.calls].count("http://api.acoustid.org/v2/lookup") == 1
    assert [c[1] for c in net.calls].count("https://example.org/submit") == 2
    s.post("http://api.acoustid.org/v2/lookup", data={"client": "SECRETKEY123", "fingerprint": "OTHER"})
    assert [c[1] for c in net.calls].count("http://api.acoustid.org/v2/lookup") == 2


def test_secrets_are_not_stored(cache):
    s = session(FakeNet())
    s.get("https://api.discogs.com/database/search?q=x&token=TOPSECRETTOKEN")
    s.post("http://api.acoustid.org/v2/lookup", data={"client": "SECRETKEY123", "fingerprint": "AQAA"})
    raw = cache.path.read_bytes() + (cache.path.with_name(cache.path.name + "-wal").read_bytes()
                                     if cache.path.with_name(cache.path.name + "-wal").exists() else b"")
    assert b"TOPSECRETTOKEN" not in raw and b"SECRETKEY123" not in raw
    assert cache.stats()["entries"] == 2


def test_expired_entries_are_fetched_again(cache, monkeypatch):
    net = FakeNet()
    s = session(net)
    s.get("https://musicbrainz.org/a")
    monkeypatch.setenv("MLT_CACHE_DAYS", "0")
    s.get("https://musicbrainz.org/a")
    assert len(net.calls) == 2


def test_deezer_misses_are_throttled_hits_are_not(cache):
    s = session(FakeNet())
    t0 = time.monotonic()
    for q in "abc":
        s.get(f"https://api.deezer.com/search?q={q}")
    assert time.monotonic() - t0 >= 2 * httpcache.THROTTLE["api.deezer.com"] * 0.9
    t0 = time.monotonic()
    for q in "abc":
        s.get(f"https://api.deezer.com/search?q={q}")
    assert time.monotonic() - t0 < 0.1


def test_musicbrainz_limiter_is_skipped_on_hits(cache):
    from requests_ratelimiter import LimiterSession
    s = LimiterSession(per_second=1)
    net = FakeNet()
    s.mount("https://", net)
    s.get("https://musicbrainz.org/ws/2/x")
    s.get("https://musicbrainz.org/ws/2/y")             # a real request: waits ~1 s
    t0 = time.monotonic()
    for _ in range(3):
        s.get("https://musicbrainz.org/ws/2/x")
        s.get("https://musicbrainz.org/ws/2/y")
    assert time.monotonic() - t0 < 0.2 and len(net.calls) == 2


def test_cache_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MLT_NO_CACHE", "1")
    assert httpcache.install(tmp_path / "x.sqlite") is None
    try:
        net = FakeNet()
        s = session(net)
        s.get("https://musicbrainz.org/a")
        s.get("https://musicbrainz.org/a")
        assert len(net.calls) == 2
    finally:
        httpcache.uninstall()


def test_fingerprints_cached_by_file_identity(cache, tmp_path, monkeypatch):
    import acoustid
    calls = []
    f = tmp_path / "a.flac"
    f.write_bytes(b"x" * 10)
    # the cache wraps whatever fingerprint_file was at install time; re-wrap a fake for the test
    httpcache.uninstall()
    monkeypatch.setattr(acoustid, "fingerprint_file", lambda p, *a, **k: calls.append(p) or (12.0, b"FP" + bytes(str(len(calls)), "ascii")))
    httpcache.install(tmp_path / "http2.sqlite")
    httpcache.fp_keys[str(f)] = httpcache.file_key(str(f), "0:120")
    assert acoustid.fingerprint_file(str(f)) == (12.0, b"FP1")
    assert acoustid.fingerprint_file(str(f)) == (12.0, b"FP1") and len(calls) == 1
    f.write_bytes(b"y" * 11)                         # the file changed: new key, fingerprinted again
    httpcache.fp_keys[str(f)] = httpcache.file_key(str(f), "0:120")
    assert acoustid.fingerprint_file(str(f)) == (12.0, b"FP2")
    assert acoustid.fingerprint_file(str(tmp_path / "unknown")) == (12.0, b"FP3")   # no key: never cached
    assert httpcache._cache.stats()["fingerprints"] == 2


def test_junk_catalogue_titles_are_compared_quickly():
    """A Discogs joke release has 274,000-character track titles; comparing must stay cheap."""
    import importlib
    from music_steward.recognize import beets_adapter as A
    A._bound_string_distance()
    D = importlib.import_module("beets.autotag.distance")
    junk = "".join(chr(i) for i in range(32, 300_000) if not 0xD800 <= i <= 0xDFFF)
    t0 = time.monotonic()
    for _ in range(20):
        D.string_dist("怀念战友", junk)
    assert time.monotonic() - t0 < 1.0
    assert D.string_dist("怀念战友", "怀念战友") == 0.0
    from music_steward.recognize.worker import _short
    assert len(_short(junk)) == 501 and _short("短") == "短"
