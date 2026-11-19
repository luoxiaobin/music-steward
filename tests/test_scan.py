import json
import os
import shutil
import unicodedata

import pytest

from mp3librarytool import audit, hashpass
from mp3librarytool import scanner as scanmod
from mp3librarytool.scanner import RootUnavailable, scan

from .conftest import files, needs_ffmpeg

pytestmark = needs_ffmpeg


def test_first_scan_records_everything(library, con):
    r = scan(con, library)
    assert r.status == "complete"
    f = files(con)
    assert "Clean Album/01 - one.mp3" in f
    assert f["Clean Album/cover.jpg"]["kind"] == "image"
    assert f["Clean Album/album.cue"]["kind"] == "cue"
    assert f["Clean Album/01 - one.lrc"]["kind"] == "lrc"
    assert ".DS_Store" not in f
    assert r.counts["n_new"] == len(f)


def test_container_and_codec_stored_separately(library, con):
    scan(con, library)
    f = files(con)
    m4a = f["Formats/a.m4a"]
    assert m4a["codec"] == "aac" and "mp4" in m4a["container"]
    assert f["Formats/a.wav"]["codec"] == "pcm_s16le" and f["Formats/a.wav"]["container"] == "wav"
    assert f["Clean Album/02 - two.flac"]["codec"] == "flac"


def test_folder_named_like_file_is_a_folder(library, con):
    scan(con, library)
    assert "Album.flac/01.flac" in files(con)
    assert [r["relpath"] for r in audit.run(con, "dirs_named_like_file")] == ["Album.flac"]


def test_unreadable_is_listed_not_fatal(library, con):
    r = scan(con, library)
    assert r.status == "complete"
    bad = audit.run(con, "unreadable")
    assert [b["relpath"] for b in bad] == ["Broken/corrupt.mp3"]
    assert bad[0]["probe_error"]


def test_rescan_skips_unchanged_files(library, con, monkeypatch):
    scan(con, library)
    calls = []
    real = scanmod._analyze
    monkeypatch.setattr(scanmod, "_analyze", lambda root, item: calls.append(item.rel) or real(root, item))
    r = scan(con, library)
    assert calls == []
    assert r.counts["n_unchanged"] == r.counts["n_files"] and r.counts["n_new"] == 0


def test_changed_file_is_reread_and_hashes_cleared(library, con):
    scan(con, library)
    hashpass.run(con)
    p = library / "Clean Album" / "02 - two.flac"
    from mutagen.flac import FLAC
    f = FLAC(p)
    f["TITLE"] = ["Two (edited)"]
    f.save()
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10_000_000))
    r = scan(con, library)
    assert r.counts["n_changed"] == 1
    row = files(con)["Clean Album/02 - two.flac"]
    assert json.loads(row["tags_json"])["title"] == "Two (edited)"
    assert row["file_sha256"] is None and row["audio_hash"] is None


def test_nfd_filename_is_stored_nfc_and_stable(library, con):
    scan(con, library)
    key = unicodedata.normalize("NFC", "Unicode/Café Ünïcode.flac")
    f = files(con)
    assert key in f
    assert unicodedata.is_normalized("NFC", key)
    assert os.path.exists(os.path.join(library, f[key]["fs_relpath"]))
    r = scan(con, library)
    assert r.counts["n_new"] == 0 and r.counts["n_changed"] == 0


def test_file_removed_between_scans(library, con):
    scan(con, library)
    (library / "Untagged" / "x.flac").unlink()
    r = scan(con, library)
    assert r.counts["n_removed"] == 1
    assert [x["relpath"] for x in audit.run(con, "removed")] == ["Untagged/x.flac"]


def test_file_removed_mid_scan(library, con):
    victim = library / "Formats" / "a.wav"

    def after_walk(found):  # listed, then deleted before it is read
        victim.unlink()
    r = scan(con, library, _hooks={"after_walk": after_walk})
    assert r.status == "complete"
    assert "Formats/a.wav" not in files(con)
    assert r.counts["n_vanished"] == 1


def test_known_file_removed_mid_scan_is_marked_removed(library, con):
    scan(con, library)
    victim = library / "Formats" / "a.wav"
    # make it look changed so the scan tries to read it, then delete it
    os.utime(victim, ns=(0, victim.stat().st_mtime_ns + 10_000_000))
    r = scan(con, library, _hooks={"after_walk": lambda found: victim.unlink()})
    assert r.status == "complete"
    assert r.counts["n_removed"] == 1


def test_root_unplugged_mid_scan_aborts_and_removes_nothing(library, con, tmp_path):
    scan(con, library)
    before = set(files(con))
    away = tmp_path / "unplugged"

    def after_list(d, entries):
        if d == "":
            library.rename(away)  # the whole volume disappears
    r = scan(con, library, _hooks={"after_list": after_list})
    assert r.status == "aborted" and "root" in r.abort_reason
    assert set(files(con)) == before          # nothing marked removed
    away.rename(library)
    r2 = scan(con, library)
    assert r2.status == "complete" and r2.counts["n_removed"] == 0


def test_root_missing_at_start(tmp_path, con):
    with pytest.raises(RootUnavailable):
        scan(con, tmp_path / "nope")


def test_unlistable_subfolder_keeps_known_files(library, con):
    scan(con, library)
    d = library / "Dupes"
    d.chmod(0)
    try:
        r = scan(con, library)
    finally:
        d.chmod(0o755)
    assert r.status == "complete"
    assert r.counts["n_dir_errors"] == 1 and r.counts["n_removed"] == 0
    assert "Dupes/copy.mp3" in files(con)
    assert [x["relpath"] for x in audit.run(con, "unlistable_dirs")] == ["Dupes"]


def test_missing_metadata_filter(library, con):
    scan(con, library)
    miss = {m["relpath"]: m["missing"] for m in audit.run(con, "missing_metadata")}
    assert miss["Untagged/x.flac"] == ["title", "artist", "album"]
    assert "Clean Album/01 - one.mp3" not in miss


def test_garbled_tags_get_a_repair_proposal(library, con):
    scan(con, library)
    g = {(x["relpath"], x["field"]): x for x in audit.run(con, "garbled_tags")}
    assert g[("Garbled/夜航.mp3", "title")]["proposal"] == "夜航"
    assert g[("Garbled/夜航.mp3", "artist")]["proposal"] == "郑绪岚"
    # the file itself is untouched
    from mutagen.id3 import ID3
    assert ID3(library / "Garbled" / "夜航.mp3")["TIT2"].text[0] != "夜航"


def test_vbr_without_xing_is_flagged_estimated(library, con):
    scan(con, library)
    f = files(con)
    assert f["NoXing/vbr.mp3"]["bit_rate_estimated"] == 1
    assert f["Clean Album/01 - one.mp3"]["bit_rate_estimated"] == 0


def test_orphan_lrc_and_cue(library, con):
    scan(con, library)
    assert [x["relpath"] for x in audit.run(con, "orphan_lrc")] == ["Orphan.lrc"]
    assert [x["relpath"] for x in audit.run(con, "cue_sheets")] == ["Clean Album/album.cue"]


def test_hashes_exact_and_same_audio_duplicates(library, con):
    scan(con, library)
    c = hashpass.run(con)
    assert c["errors"] == 0 and c["remaining"] == 0
    exact = audit.run(con, "exact_duplicates")
    assert [g["files"] for g in exact] == [["Clean Album/01 - one.mp3", "Dupes/copy.mp3"]]
    same = audit.run(con, "same_audio")
    assert same and set(same[0]["files"]) == {"Clean Album/01 - one.mp3", "Dupes/copy.mp3",
                                              "Dupes/retagged.mp3"}


def test_hash_pass_is_resumable(library, con):
    scan(con, library)
    first = hashpass.run(con, limit=3)
    assert first["hashed"] == 3 and first["remaining"] > 0
    rest = hashpass.run(con)
    assert rest["remaining"] == 0
    assert hashpass.run(con)["hashed"] == 0


def test_scan_never_writes_to_the_collection(library, con):
    def snap():
        out = {}
        for root, _, fs in os.walk(library):
            for n in fs:
                p = os.path.join(root, n)
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns, open(p, "rb").read())
        return out
    before = snap()
    scan(con, library)
    hashpass.run(con)
    assert snap() == before


def test_ctrl_c_stops_promptly_keeps_progress_marks_nothing_removed(library, con, monkeypatch):
    scan(con, library)
    for p in (library / "Clean Album").iterdir():  # make some files 'changed'
        os.utime(p, ns=(0, p.stat().st_mtime_ns + 10_000_000))
    real = scanmod._analyze
    calls = {"n": 0}

    def flaky(root, item):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(root, item)
    monkeypatch.setattr(scanmod, "_analyze", flaky)
    with pytest.raises(KeyboardInterrupt):
        scan(con, library, workers=1)
    assert con.execute("SELECT status FROM scan ORDER BY id DESC LIMIT 1").fetchone()[0] == "interrupted"
    assert not audit.run(con, "removed")
    monkeypatch.setattr(scanmod, "_analyze", real)
    r = scan(con, library)
    assert r.status == "complete" and r.counts["n_removed"] == 0


def test_stale_running_scan_is_marked_interrupted(library, con):
    con.execute("INSERT INTO root(path, added_at) VALUES(?, 0)", (str(library),))
    con.execute("INSERT INTO scan(root_id, started_at, status) VALUES(1, 0, 'running')")
    con.commit()
    scan(con, library)
    assert con.execute("SELECT status FROM scan WHERE id=1").fetchone()[0] == "interrupted"
