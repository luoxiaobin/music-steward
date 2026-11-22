"""User settings, kept in the app database (table `meta`, keys 'setting:<name>').

An environment variable, when set, overrides the stored value (scripts, tests, one-off runs).
Secrets (API keys, tokens) are never settings: they stay in the environment.
"""
from __future__ import annotations

import os
import sqlite3

# name: (default, allowed values or None for free text, environment override, label)
SPEC = {
    "script": ("simplified", ("simplified", "traditional", "keep"), "MLT_SCRIPT",
               "How artist and album names are written"),
    "year": ("original", ("original", "release"), "MLT_YEAR", "Which year folder names and tags use"),
    "cue_encoding": ("utf-8-sig", ("utf-8-sig", "original"), "MLT_CUE_ENCODING", "Encoding of copied cue sheets"),
    "album_cue": ("on", ("on", "off"), "MLT_ALBUM_CUE", "Cue sheet for albums of separate track files"),
    "cue_albums": ("image", ("image", "tracks"), "MLT_CUE_ALBUMS",
                   "Albums of one file with a cue sheet: keep one file, or split into tracks"),
    "sources": ("musicbrainz,deezer,discogs", None, "MLT_SOURCES", "Online catalogues to search"),
    "dest": ("", None, None, "Default destination folder"),
    "retire_cooloff_days": ("7", ("0", "1", "3", "7", "14", "30"), None,
                            "Days after setting originals aside before deleting them needs an extra confirmation"),
    "language": ("auto", ("auto", "en", "zh-hans", "zh-hant", "fr-ca", "es-us"), "MLT_LANGUAGE", "Interface language"),
}
_ENV_TRUE = {"1": "on", "0": "off"}   # MLT_ALBUM_CUE=1/0


def _env(name: str) -> str | None:
    env = SPEC[name][2]
    v = os.environ.get(env) if env else None
    if v is None:
        return None
    return _ENV_TRUE.get(v, v) if name == "album_cue" else v


def get(con: sqlite3.Connection | None, name: str) -> str:
    default, allowed, _env_name, _label = SPEC[name]
    v = _env(name)
    if v is None and con is not None:
        row = con.execute("SELECT value FROM meta WHERE key=?", (f"setting:{name}",)).fetchone()
        v = row[0] if row else None
    if v is None:
        return default
    if allowed and v.lower() not in allowed:
        return default
    return v.lower() if allowed else v


def all_values(con: sqlite3.Connection) -> dict:
    return {n: {"value": get(con, n), "default": SPEC[n][0], "choices": SPEC[n][1], "label": SPEC[n][3],
                "overridden_by": SPEC[n][2] if _env(n) is not None else None} for n in SPEC}


def retire_allowed(con: sqlite3.Connection | None, root_id: int) -> bool:
    """Retiring originals (ADR 0003) is off unless the user allowed it for this collection folder."""
    if con is None:
        return False
    row = con.execute("SELECT value FROM meta WHERE key=?", (f"setting:retire_root:{int(root_id)}",)).fetchone()
    return bool(row and row[0] == "on")


def set_retire_allowed(con: sqlite3.Connection, root_id: int, allowed: bool) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)",
                (f"setting:retire_root:{int(root_id)}", "on" if allowed else "off"))
    con.commit()


class SettingsError(ValueError):
    pass


def save(con: sqlite3.Connection, values: dict) -> dict:
    for n, v in values.items():
        if n not in SPEC:
            raise SettingsError(f"unknown setting {n}")
        v = "" if v is None else str(v).strip()
        allowed = SPEC[n][1]
        if allowed and v.lower() not in allowed:
            raise SettingsError(f"{n} must be one of: {', '.join(allowed)}")
        if n == "sources":
            parts = [p.strip().lower() for p in v.split(",") if p.strip()]
            if any(p not in ("musicbrainz", "deezer", "discogs") for p in parts):
                raise SettingsError("sources: musicbrainz, deezer, discogs")
            v = ",".join(["musicbrainz"] + [p for p in ("deezer", "discogs") if p in parts])
        if n == "dest" and v:
            from . import folders
            r = folders.check_folder(con, v, "destination")
            if r["level"] == "error":
                raise SettingsError(folders.explain(r))
            v = r.get("resolved") or os.path.abspath(os.path.expanduser(v))
        con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (f"setting:{n}", v.lower() if allowed else v))
    con.commit()
    return all_values(con)
