"""Previews: formats a browser cannot play, and single tracks inside a cue image."""
import os
import subprocess

import pytest

from music_steward import preview

from .conftest import needs_ffmpeg, tone

pytestmark = needs_ffmpeg


def _dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout
    return float(out)


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MLT_PREVIEW_DIR", str(tmp_path / "cache"))


def test_slice_of_an_image_and_reuse(tmp_path):
    img = tone(tmp_path / "image.flac", ["-c:a", "flac"], secs=6)
    p = preview.make(str(img), start=2.0, duration=2.0)
    assert p.suffix == ".mp3" and abs(_dur(p) - 2.0) < 0.15
    mtime = p.stat().st_mtime_ns
    assert preview.make(str(img), start=2.0, duration=2.0) == p and p.stat().st_mtime_ns >= mtime   # cached
    whole = preview.make(str(img))
    assert whole != p and abs(_dur(whole) - 6.0) < 0.2
    assert img.read_bytes()[:4] == b"fLaC"                                         # source untouched


def test_cache_is_pruned_to_its_limit(tmp_path, monkeypatch):
    img = tone(tmp_path / "image.flac", ["-c:a", "flac"], secs=6)
    monkeypatch.setattr(preview, "MAX_CACHE_BYTES", 1)                             # keep only the newest
    a = preview.make(str(img), start=0.0, duration=1.0)
    b = preview.make(str(img), start=1.0, duration=1.0)
    assert b.exists() and not a.exists()


def test_unreadable_file_gives_a_clear_error(tmp_path):
    bad = tmp_path / "bad.ape"
    bad.write_bytes(b"MAC " + os.urandom(2000))
    with pytest.raises(preview.PreviewError):
        preview.make(str(bad))
    assert not list((tmp_path / "cache").glob("*.mp3"))
