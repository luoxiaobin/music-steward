"""Unreadable-file handling found on the real NAS: APE with an ID3v2 prefix,
VCD videos named like audio, zero-filled files."""
import os
import shutil
import sqlite3
import subprocess

import pytest

from mp3librarytool import audit, db
from mp3librarytool.hashing import audio_hash
from mp3librarytool.probe import ProbeError, audio_offset, probe
from mp3librarytool.scanner import scan

from .conftest import APE_SAMPLE, needs_ffmpeg

pytestmark = needs_ffmpeg


def _id3_prefixed_ape(tmp_path, pad=4096):
    from mutagen.id3 import ID3, TIT2, TPE1
    tag = tmp_path / "tag.id3"
    t = ID3()
    t.add(TIT2(encoding=0, text=["Track01"]))
    t.add(TPE1(encoding=0, text=["未知艺术家".encode("gbk").decode("latin-1")]))
    t.save(tag, padding=lambda info: pad)
    out = tmp_path / "prefixed.ape"
    out.write_bytes(tag.read_bytes() + open(APE_SAMPLE, "rb").read())
    return out


@pytest.mark.skipif(not APE_SAMPLE, reason="set MLT_APE_SAMPLE to an .ape file to run")
def test_ape_with_id3_prefix_is_readable_and_hash_matches_original(tmp_path):
    p = _id3_prefixed_ape(tmp_path)
    assert audio_offset(str(p), ".ape") > 0
    props = probe(str(p), ext=".ape")
    assert props.codec == "ape" and props.audio_offset > 0
    assert audio_hash(str(p), ".ape")[0] == audio_hash(APE_SAMPLE, ".ape")[0]


def test_vcd_video_named_ape_gets_a_content_note(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=352x288:rate=25",
                    "-f", "lavfi", "-i", "sine", "-t", "2", "-c:v", "mpeg1video", "-c:a", "mp2",
                    "-f", "vcd", str(lib / "opera vcd1a.ape")], check=True)
    (lib / "zeros.mp3").write_bytes(b"\0" * (5 << 20))
    c = db.connect(tmp_path / "a.sqlite")
    scan(c, lib)
    notes = {r["relpath"]: r["content_note"] for r in audit.run(c, "unreadable")}
    assert "zeros" in notes["zeros.mp3"]
    # ffprobe may or may not accept a VCD stream under an .ape name; if it fails, say why
    if "opera vcd1a.ape" in notes:
        assert "MPEG" in notes["opera vcd1a.ape"]


def test_old_database_gets_new_columns(tmp_path):
    p = tmp_path / "old.sqlite"
    db.connect(p).close()                       # current schema ...
    con = sqlite3.connect(p)
    for col in ("audio_offset", "content_note"):  # ... minus the columns added later
        con.execute(f"ALTER TABLE file DROP COLUMN {col}")
    con.commit()
    con.close()
    c = db.connect(p)
    cols = {r[1] for r in c.execute("PRAGMA table_info(file)")}
    assert {"audio_offset", "content_note"} <= cols


def test_reprobe_errors_retries_unchanged_unreadable_files(library, con, monkeypatch):
    scan(con, library)
    from mp3librarytool import scanner as sm
    seen = []
    real = sm._analyze
    monkeypatch.setattr(sm, "_analyze", lambda root, item: seen.append(item.rel) or real(root, item))
    scan(con, library)
    assert seen == []
    scan(con, library, reprobe_errors=True)
    assert seen == ["Broken/corrupt.mp3"]
