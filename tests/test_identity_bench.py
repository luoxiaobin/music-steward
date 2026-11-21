"""tools/identity_bench.py: the measured identities must be independent of tags (ADR 0002)."""
import importlib.util
from pathlib import Path

import pytest

from .conftest import needs_ffmpeg, tone

pytestmark = needs_ffmpeg

spec = importlib.util.spec_from_file_location("identity_bench", Path(__file__).parents[1] / "tools/identity_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.fixture(params=[(0.0, 0.25, 0.5, 0.75), (0.0,)], ids=["sampled", "edges"])
def mode(request, monkeypatch):
    monkeypatch.setattr(bench, "FRACTIONS", request.param)


def test_identity_ignores_tags_even_when_the_audio_moves(tmp_path, mode):
    from mutagen.flac import FLAC
    from mutagen.id3 import APIC, ID3, TIT2
    f = tone(tmp_path / "a.flac", ["-c:a", "flac"], secs=4)
    m = tone(tmp_path / "a.mp3", ["-c:a", "libmp3lame"], secs=4)
    before = [bench.sampled_identity(str(p), p.suffix, None) for p in (f, m)]
    x = FLAC(f)
    x["COMMENT"] = "z" * 200_000                  # beyond the padding: the audio moves
    x.save()
    y = ID3()
    y.add(TIT2(encoding=3, text="新标题"))
    y.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="c", data=b"\xff" * 300_000))
    y.save(m)
    assert [bench.sampled_identity(str(p), p.suffix, None) for p in (f, m)] == before
    g = tone(tmp_path / "b.flac", ["-c:a", "flac"], secs=4, freq=900)
    assert bench.sampled_identity(str(g), ".flac", None) != before[0]


def test_short_audio_never_reads_into_trailing_tags(tmp_path, mode):
    """Codex review on #5: a region shorter than the slices read into an appended ID3v1 tag."""
    m = tone(tmp_path / "short.mp3", ["-c:a", "libmp3lame", "-b:a", "32k"], secs=1)
    assert bench.audio_region(str(m), ".mp3", None)[1] < 256 * 1024
    before = bench.sampled_identity(str(m), ".mp3", None)
    with open(m, "ab") as fh:
        fh.write(b"TAG" + b"x" * 125)              # an ID3v1 tag appended
    assert bench.audio_region(str(m), ".mp3", None)[1] == m.stat().st_size - 128
    assert bench.sampled_identity(str(m), ".mp3", None) == before
