"""App database (SQLite). The collection is never written; only this file is."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

APP_DIR = Path.home() / ".music-steward"
OLD_APP_DIR = Path.home() / ".mp3librarytool"   # name until 2026-11-19
DEFAULT_DB = Path(os.environ.get("MLT_DB", APP_DIR / "library.sqlite"))


def migrate_app_dir() -> bool:
    """Move ~/.mp3librarytool to ~/.music-steward once, and rewrite the journal paths that
    past runs recorded, so Undo / Resume of old runs keep working."""
    if not OLD_APP_DIR.exists() or APP_DIR.exists():
        return False
    OLD_APP_DIR.rename(APP_DIR)
    dbp = APP_DIR / "library.sqlite"
    if dbp.exists():
        con = sqlite3.connect(dbp)
        try:
            con.execute("UPDATE run SET journal = replace(journal, ?, ?)", (str(OLD_APP_DIR), str(APP_DIR)))
            con.commit()
        except sqlite3.OperationalError:
            pass  # no run table yet
        con.close()
    return True

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
    status TEXT NOT NULL,               -- running | complete | aborted | interrupted
    subpath TEXT,                       -- '' = whole root, else the sub-folder scanned
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

-- recognition (M2): a unit is an album (or one loose track) queued for matching
CREATE TABLE IF NOT EXISTS unit(
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES root(id),
    folder TEXT NOT NULL,               -- NFC relpath of the folder the unit lives in
    label TEXT NOT NULL,                -- album tag or folder/file name, for display
    kind TEXT NOT NULL,                 -- album | track
    key TEXT NOT NULL,                  -- stable identity: kind + folder + album tag / file
    file_ids TEXT NOT NULL,             -- JSON list of file.id
    status TEXT NOT NULL,               -- queued | running | done | error
    recommendation TEXT, acoustid_used INTEGER, files_linked INTEGER,
    error TEXT, queued_at REAL, started_at REAL, finished_at REAL,
    UNIQUE(root_id, key)
);
CREATE TABLE IF NOT EXISTS candidate(
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES unit(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL, source TEXT, album_id TEXT, album TEXT, artist TEXT,
    year INTEGER, country TEXT, label TEXT, media TEXT, mediums INTEGER,
    distance REAL, penalties_json TEXT, extra_items_json TEXT, extra_tracks_json TEXT,
    fingerprint_agrees INTEGER, n_mapped INTEGER
);
CREATE TABLE IF NOT EXISTS cand_track(
    candidate_id INTEGER NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES file(id),
    recording_id TEXT, release_track_id TEXT, track_index INTEGER, medium INTEGER,
    medium_index INTEGER, title TEXT, artist TEXT, length REAL, track_distance REAL,
    PRIMARY KEY(candidate_id, file_id)
);
-- track mapping for cue-sheet image albums: many tracks live in ONE audio file
CREATE TABLE IF NOT EXISTS cand_cue_track(
    candidate_id INTEGER NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    cue_track INTEGER NOT NULL, cue_title TEXT,
    recording_id TEXT, release_track_id TEXT, track_index INTEGER, medium INTEGER,
    medium_index INTEGER, title TEXT, artist TEXT, length REAL, track_distance REAL,
    PRIMARY KEY(candidate_id, cue_track)
);
CREATE TABLE IF NOT EXISTS decision(
    unit_id INTEGER PRIMARY KEY REFERENCES unit(id) ON DELETE CASCADE,
    action TEXT NOT NULL,               -- accept | skip
    candidate_id INTEGER REFERENCES candidate(id),
    decided_at REAL NOT NULL
);

-- plans and runs (M3 slice): a plan is frozen; a run applies it with a journal
CREATE TABLE IF NOT EXISTS plan(
    id INTEGER PRIMARY KEY, created_at REAL NOT NULL, dest_root TEXT NOT NULL,
    root_id INTEGER, scope TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS plan_item(
    plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, unit_id INTEGER, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
    src TEXT NOT NULL, dest_rel TEXT NOT NULL, codec TEXT, tags_json TEXT,
    src_size INTEGER, src_mtime_ns INTEGER,
    status TEXT NOT NULL,               -- ok | conflict_existing | conflict_duplicate | source_changed
    note TEXT,
    PRIMARY KEY(plan_id, seq)
);
CREATE TABLE IF NOT EXISTS run(
    id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plan(id), run_key TEXT NOT NULL,
    journal TEXT NOT NULL, method TEXT, started_at REAL, finished_at REAL, summary_json TEXT,
    undone_at REAL
);

CREATE TABLE IF NOT EXISTS dir_note(
    root_id INTEGER NOT NULL, relpath TEXT NOT NULL, note TEXT NOT NULL, detail TEXT,
    last_scan INTEGER,
    PRIMARY KEY(root_id, relpath, note)
);
"""


ADDED_COLUMNS = [("file", "audio_offset", "INTEGER"), ("file", "content_note", "TEXT"),
                 ("scan", "subpath", "TEXT"), ("unit", "repaired", "INTEGER"),
                 ("decision", "release_key", "TEXT"), ("unit", "notice", "TEXT"),
                 ("unit", "search_json", "TEXT"), ("decision", "overrides_json", "TEXT"),
                 ("unit", "cue_file_id", "INTEGER"), ("unit", "n_tracks", "INTEGER"),
                 ("candidate", "origin_json", "TEXT"), ("candidate", "original_year", "INTEGER")]


_READY: set[str] = set()   # databases already set up by this process


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Open the app database. Schema setup and migrations run once per process and database:
    the web server opens a connection per request, and running the setup's writes on every
    one made concurrent requests block each other ("database is locked")."""
    path = Path(path)
    key = str(path.resolve()) if path.exists() else str(path)
    if key in _READY:
        con = sqlite3.connect(path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con
    if path == DEFAULT_DB:
        migrate_app_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    for table, col, typ in ADDED_COLUMNS:
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # runs recorded before the rename point at journals under the old folder (only if any do)
    if not OLD_APP_DIR.exists() and APP_DIR.exists() and con.execute(
            "SELECT 1 FROM run WHERE journal LIKE ? LIMIT 1", (str(OLD_APP_DIR) + "%",)).fetchone():
        con.execute("UPDATE run SET journal = replace(journal, ?, ?) WHERE journal LIKE ?",
                    (str(OLD_APP_DIR), str(APP_DIR), str(OLD_APP_DIR) + "%"))
    con.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()
    _READY.add(str(path.resolve()))
    return con
