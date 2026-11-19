"""Synthetic fixture library, generated with ffmpeg at test time (no real music)."""
from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from music_steward import db as dbmod

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
# There is no free APE encoder on Homebrew; point this at any .ape you own to run APE tests.
APE_SAMPLE = os.environ.get("MLT_APE_SAMPLE")


def tone(path: Path, codec_args: list[str], secs: float = 3, freq: int = 440, extra=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={secs}", *codec_args, *extra,
                    "-map_metadata", "-1", str(path)], check=True)
    return path


def tag_id3(path: Path, title, artist="Artist", album="Album", encoding=3):
    from mutagen.id3 import ID3, TALB, TIT2, TPE1
    t = ID3()
    t.add(TIT2(encoding=encoding, text=[title]))
    t.add(TPE1(encoding=encoding, text=[artist]))
    if album:
        t.add(TALB(encoding=encoding, text=[album]))
    t.save(path)


@pytest.fixture
def library(tmp_path) -> Path:
    """
    lib/
      Clean Album/01 - one.mp3, 02 - two.flac, cover.jpg, album.cue, 01 - one.lrc
      Formats/a.m4a, a.wav
      Garbled/夜航.mp3            ID3 Latin-1 frames holding GBK bytes
      Broken/corrupt.mp3          random bytes
      Dupes/copy.mp3              exact copy of Clean Album/01 - one.mp3
      Dupes/retagged.mp3          same audio, different tags
      NoXing/vbr.mp3              VBR without a Xing header
      Album.flac/01.flac          a FOLDER named like a file
      Untagged/x.flac             no tags
      <NFD-named file>            name stored in decomposed form
      .DS_Store                   junk, ignored
    """
    lib = tmp_path / "lib"
    a = lib / "Clean Album"
    tone(a / "01 - one.mp3", ["-c:a", "libmp3lame", "-b:a", "128k"])
    tag_id3(a / "01 - one.mp3", "One")
    tone(a / "02 - two.flac", ["-c:a", "flac"], freq=550)
    from mutagen.flac import FLAC
    f = FLAC(a / "02 - two.flac")
    f["TITLE"], f["ARTIST"], f["ALBUM"] = ["Two"], ["Artist"], ["Album"]
    f.save()
    (a / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    (a / "album.cue").write_text('FILE "01 - one.mp3" MP3\n', encoding="utf-8")
    (a / "01 - one.lrc").write_text("[00:00.00]la\n", encoding="utf-8")
    (lib / "Orphan.lrc").write_text("[00:00.00]no audio\n", encoding="utf-8")

    tone(lib / "Formats" / "a.m4a", ["-c:a", "aac"], freq=660)
    tone(lib / "Formats" / "a.wav", ["-c:a", "pcm_s16le"], freq=770)

    g = tone(lib / "Garbled" / "夜航.mp3", ["-c:a", "libmp3lame"], freq=880)
    tag_id3(g, "夜航".encode("gbk").decode("latin-1"), artist="郑绪岚".encode("gbk").decode("latin-1"),
            album=None, encoding=0)

    (lib / "Broken").mkdir()
    (lib / "Broken" / "corrupt.mp3").write_bytes(os.urandom(4096))

    (lib / "Dupes").mkdir()
    shutil.copy2(a / "01 - one.mp3", lib / "Dupes" / "copy.mp3")
    shutil.copy2(a / "01 - one.mp3", lib / "Dupes" / "retagged.mp3")
    tag_id3(lib / "Dupes" / "retagged.mp3", "Different title", artist="Someone")

    tone(lib / "NoXing" / "vbr.mp3", ["-c:a", "libmp3lame", "-q:a", "2"], secs=4,
         extra=["-write_xing", "0"])
    tone(lib / "Album.flac" / "01.flac", ["-c:a", "flac"], freq=990)
    tone(lib / "Untagged" / "x.flac", ["-c:a", "flac"], freq=1100)

    nfd = unicodedata.normalize("NFD", "Café Ünïcode.flac")
    tone(lib / "Unicode" / nfd, ["-c:a", "flac"], freq=1200)
    (lib / ".DS_Store").write_bytes(b"junk")
    return lib


@pytest.fixture
def con(tmp_path):
    c = dbmod.connect(tmp_path / "app.sqlite")
    yield c
    c.close()


def files(con, live_only=True):
    q = "SELECT * FROM file" + (" WHERE removed_at IS NULL" if live_only else "")
    return {r["relpath"]: r for r in con.execute(q)}
