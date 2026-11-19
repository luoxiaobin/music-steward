"""App database (SQLite). The collection is never written; only this file is."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("MLT_DB", Path.home() / ".mp3librarytool" / "library.sqlite"))

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS root(
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,          -- absolute, NFC
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scan(
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES root(id),
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,               -- running | complete | aborted
    abort_reason TEXT,
    n_files INTEGER DEFAULT 0, n_new INTEGER DEFAULT 0, n_changed INTEGER DEFAULT 0,
    n_unchanged INTEGER DEFAULT 0, n_removed INTEGER DEFAULT 0, n_vanished INTEGER DEFAULT 0,
    n_probe_errors INTEGER DEFAULT 0, n_dir_errors INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS file(
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES root(id),
    relpath TEXT NOT NULL,              -- NFC key, '/'-separated, relative to root
    fs_relpath TEXT NOT NULL,           -- exactly as the filesystem returned it (for opening)
    kind TEXT NOT NULL,                 -- audio | cue | lrc | image | other
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    first_scan INTEGER, last_scan INTEGER,
    removed_at REAL,                    -- set when a COMPLETE scan no longer finds it
    -- technical properties (ffprobe), audio only
    probe_status TEXT,                  -- ok | error | NULL(not audio)
    probe_error TEXT,
    container TEXT, codec TEXT,
    duration REAL, duration_estimated INTEGER,
    bit_rate INTEGER, bit_rate_estimated INTEGER,
    sample_rate INTEGER, channels INTEGER, bits_per_sample INTEGER,
    audio_offset INTEGER,               -- bytes skipped before the audio header (APE + ID3 prefix)
    content_note TEXT,                  -- why an 'audio' file is unreadable, from its content
    -- tags (cache of what is in the file)
    tag_format TEXT, tags_json TEXT, tag_issues_json TEXT,
    -- hashes (filled lazily by the hash pass; cleared when size/mtime change)
    file_sha256 TEXT, audio_hash TEXT, audio_hash_method TEXT, hash_error TEXT,
    hashed_size INTEGER, hashed_mtime_ns INTEGER,
    UNIQUE(root_id, relpath)
);
CREATE INDEX IF NOT EXISTS file_sha ON file(file_sha256);
CREATE INDEX IF NOT EXISTS file_audio ON file(audio_hash);

CREATE TABLE IF NOT EXISTS dir_note(
    root_id INTEGER NOT NULL, relpath TEXT NOT NULL, note TEXT NOT NULL, detail TEXT,
    last_scan INTEGER,
    PRIMARY KEY(root_id, relpath, note)
);
"""


ADDED_COLUMNS = [("audio_offset", "INTEGER"), ("content_note", "TEXT")]


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(file)")}
    for col, typ in ADDED_COLUMNS:
        if col not in have:
            con.execute(f"ALTER TABLE file ADD COLUMN {col} {typ}")
    con.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()
    return con
