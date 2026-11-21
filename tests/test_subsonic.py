"""Required feature (2026-11-21): the organized copies work in Subsonic-standard music servers.

A throwaway Navidrome (the reference Subsonic server) is pointed at an organized copy; its Subsonic
API must then list every album once, with all its tracks in order and the titles, artists, years and
durations the plan wrote. Covers albums of separate track files and cue-sheet albums split into
tracks (albums kept as one file + cue sheet need a cue-aware player and are out of scope).

Skipped when `navidrome` is not installed (CI installs it on macOS: `brew install navidrome`).
"""
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from contextlib import contextmanager

import pytest

from music_steward.apply import plan, runner

from .conftest import needs_ffmpeg
from .test_apply import accepted  # noqa: F401  (fixture: an album of two MP3 files, Chinese tags)
from .test_split import split_album  # noqa: F401  (fixture: a lossless image + 3-track cue, split setting on)

pytestmark = [needs_ffmpeg,
              pytest.mark.skipif(shutil.which("navidrome") is None, reason="navidrome is not installed")]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def navidrome(music: str, tmp):
    """A throwaway Navidrome on 127.0.0.1 with a test account made up for this run."""
    port, data = _port(), tmp / "navidrome_data"
    data.mkdir()
    env = {**os.environ, "ND_MUSICFOLDER": music, "ND_DATAFOLDER": str(data), "ND_ADDRESS": "127.0.0.1",
           "ND_PORT": str(port), "ND_ENABLEINSIGHTSCOLLECTOR": "false", "ND_LOGLEVEL": "error"}
    proc = subprocess.Popen(["navidrome"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/app/", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        user, password = "test", secrets.token_hex(12)
        req = urllib.request.Request(base + "/auth/createAdmin", method="POST",
                                     data=json.dumps({"username": user, "password": password}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)

        def api(method: str, **params):
            salt = secrets.token_hex(6)
            q = {"u": user, "t": hashlib.md5((password + salt).encode()).hexdigest(), "s": salt,
                 "v": "1.16.1", "c": "music-steward-test", "f": "json", **params}
            with urllib.request.urlopen(f"{base}/rest/{method}?{urllib.parse.urlencode(q)}", timeout=10) as r:
                body = json.load(r)["subsonic-response"]
            assert body["status"] == "ok", body
            return body

        for _ in range(150):                  # the start-up scan
            if not api("getScanStatus")["scanStatus"]["scanning"] and \
                    api("getAlbumList2", type="alphabeticalByName").get("albumList2", {}).get("album"):
                break
            time.sleep(0.2)
        yield api
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _expected(p: dict) -> dict:
    """{album folder: {album, artist, year, tracks: [(number, title, seconds)]}} from the plan."""
    import subprocess as sp
    out = defaultdict(lambda: {"tracks": []})
    for it in p["items"]:
        if it["kind"] not in ("audio", "split") or it["status"] != "ok":
            continue
        t, folder = it["tags"], it["dest_rel"].rsplit("/", 1)[0]
        a = out[folder]
        a.update(album=t["album"], artist=t["albumartist"], year=t.get("date"))
        a["tracks"].append((int(t["track"]), t["title"], it["dest_rel"]))
    for a in out.values():
        a["tracks"].sort()
    return out


def _check(api, p: dict, dest):
    want = _expected(p)
    albums = api("getAlbumList2", type="alphabeticalByName", size=500)["albumList2"]["album"]
    assert len(albums) == len(want), [a["name"] for a in albums]      # each album once, none split up
    for a in want.values():
        got = next((x for x in albums if x["name"] == a["album"]), None)
        assert got is not None, (a["album"], [x["name"] for x in albums])
        assert got["artist"] == a["artist"]
        if a["year"]:
            assert got.get("year") == int(a["year"])
        songs = api("getAlbum", id=got["id"])["album"]["song"]
        assert [(s["track"], s["title"]) for s in songs] == [(n, title) for n, title, _ in a["tracks"]]
        for s, (_n, _t, rel) in zip(songs, a["tracks"]):
            secs = float(json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(dest / rel)],
                capture_output=True, check=True).stdout)["format"]["duration"])
            assert abs(s["duration"] - secs) <= 1, (s["title"], s["duration"], secs)


def test_an_album_of_track_files_is_served_correctly(accepted, con, tmp_path):  # noqa: F811
    lib, dest = accepted
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert set(runner.apply(con, p["id"], journal_root=tmp_path / "j")["states"]) == {"committed"}
    with navidrome(str(dest), tmp_path) as api:
        _check(api, p, dest)


def test_a_cue_album_split_into_tracks_is_served_correctly(split_album, con, tmp_path):  # noqa: F811
    dest = tmp_path / "out"
    p = plan.load(con, plan.build(con, str(dest), 1))
    assert set(runner.apply(con, p["id"], journal_root=tmp_path / "j")["states"]) == {"committed"}
    with navidrome(str(dest), tmp_path) as api:
        _check(api, p, dest)
