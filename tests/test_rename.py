"""The app was renamed from MP3LibraryTool to Music Steward: data folder migration."""
import sqlite3

from music_steward import db


def test_old_app_dir_is_moved_and_journal_paths_rewritten(tmp_path, monkeypatch):
    old, new = tmp_path / ".mp3librarytool", tmp_path / ".music-steward"
    monkeypatch.setattr(db, "OLD_APP_DIR", old)
    monkeypatch.setattr(db, "APP_DIR", new)
    (old / "journal").mkdir(parents=True)
    (old / "journal" / "run-1.jsonl").write_text("{}\n")
    con = sqlite3.connect(old / "library.sqlite")
    con.execute("CREATE TABLE run(id INTEGER PRIMARY KEY, journal TEXT)")
    con.execute("INSERT INTO run(journal) VALUES(?)", (str(old / "journal" / "run-1.jsonl"),))
    con.commit()
    con.close()
    assert db.migrate_app_dir() is True
    assert not old.exists() and (new / "journal" / "run-1.jsonl").exists()
    j = sqlite3.connect(new / "library.sqlite").execute("SELECT journal FROM run").fetchone()[0]
    assert j == str(new / "journal" / "run-1.jsonl")
    assert db.migrate_app_dir() is False          # only once


def test_new_dir_wins_if_both_exist(tmp_path, monkeypatch):
    old, new = tmp_path / ".mp3librarytool", tmp_path / ".music-steward"
    old.mkdir()
    new.mkdir()
    monkeypatch.setattr(db, "OLD_APP_DIR", old)
    monkeypatch.setattr(db, "APP_DIR", new)
    assert db.migrate_app_dir() is False and old.exists()   # never overwrite or merge silently
