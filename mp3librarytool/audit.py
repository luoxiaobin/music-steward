"""Audit filters over the inventory (no network, no file access)."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict

FILTERS = ("unreadable", "missing_metadata", "garbled_tags", "estimated_values",
           "exact_duplicates", "same_audio", "orphan_lrc", "cue_sheets",
           "dirs_named_like_file", "unlistable_dirs", "removed")


def _where(root_id):
    return ("AND f.root_id=?", (root_id,)) if root_id is not None else ("", ())


def run(con: sqlite3.Connection, name: str, root_id: int | None = None) -> list[dict]:
    w, a = _where(root_id)
    live = "f.removed_at IS NULL"
    if name == "unreadable":
        return [dict(r) for r in con.execute(
            f"SELECT relpath, content_note, probe_error FROM file f WHERE {live} AND kind='audio' "
            f"AND probe_status='error' {w} ORDER BY relpath", a)]
    if name == "missing_metadata":
        out = []
        for r in con.execute(f"SELECT relpath, tags_json FROM file f WHERE {live} AND kind='audio' "
                             f"AND probe_status='ok' {w} ORDER BY relpath", a):
            t = json.loads(r["tags_json"] or "{}")
            miss = [k for k in ("title", "artist", "album") if not t.get(k)]
            if miss:
                out.append({"relpath": r["relpath"], "missing": miss})
        return out
    if name == "garbled_tags":
        out = []
        for r in con.execute(f"SELECT relpath, tag_issues_json FROM file f WHERE {live} "
                             f"AND tag_issues_json IS NOT NULL {w} ORDER BY relpath", a):
            for i in json.loads(r["tag_issues_json"]):
                out.append({"relpath": r["relpath"], "field": i.get("field"), "kind": i.get("kind"),
                            "shown_as": i.get("shown_as"), "proposal": i.get("proposal"),
                            "encoding": i.get("encoding")})
        return out
    if name == "estimated_values":
        return [dict(r) for r in con.execute(
            f"SELECT relpath, codec, duration, bit_rate, duration_estimated, bit_rate_estimated "
            f"FROM file f WHERE {live} AND (duration_estimated=1 OR bit_rate_estimated=1) {w} "
            f"ORDER BY relpath", a)]
    if name in ("exact_duplicates", "same_audio"):
        col = "file_sha256" if name == "exact_duplicates" else "audio_hash"
        groups = defaultdict(list)
        for r in con.execute(f"SELECT relpath, {col} AS h, file_sha256, size FROM file f WHERE {live} "
                             f"AND {col} IS NOT NULL {w}", a):
            groups[r["h"]].append(r)
        out = []
        for h, rows in groups.items():
            if len(rows) < 2:
                continue
            if name == "same_audio" and len({r["file_sha256"] for r in rows}) < 2:
                continue  # identical files are reported as exact duplicates instead
            out.append({"hash": h[:16], "files": sorted(r["relpath"] for r in rows),
                        "size": rows[0]["size"]})
        return sorted(out, key=lambda g: g["files"][0])
    if name == "orphan_lrc":
        audio = {os.path.splitext(r["relpath"])[0] for r in con.execute(
            f"SELECT relpath FROM file f WHERE {live} AND kind='audio' {w}", a)}
        return [{"relpath": r["relpath"]} for r in con.execute(
            f"SELECT relpath FROM file f WHERE {live} AND kind='lrc' {w} ORDER BY relpath", a)
            if os.path.splitext(r["relpath"])[0] not in audio]
    if name == "cue_sheets":
        return [{"relpath": r["relpath"]} for r in con.execute(
            f"SELECT relpath FROM file f WHERE {live} AND kind='cue' {w} ORDER BY relpath", a)]
    if name in ("dirs_named_like_file", "unlistable_dirs"):
        note = "dir_named_like_file" if name == "dirs_named_like_file" else "unlistable"
        q = "SELECT relpath, detail FROM dir_note WHERE note=?" + (" AND root_id=?" if root_id else "")
        return [dict(r) for r in con.execute(q + " ORDER BY relpath", (note, *a))]
    if name == "removed":
        return [dict(r) for r in con.execute(
            f"SELECT relpath, datetime(removed_at,'unixepoch') AS removed FROM file f "
            f"WHERE f.removed_at IS NOT NULL {w} ORDER BY relpath", a)]
    raise ValueError(f"unknown filter {name}; choose from {', '.join(FILTERS)}")


def summary(con: sqlite3.Connection, root_id: int | None = None) -> dict:
    w, a = _where(root_id)
    row = con.execute(
        f"SELECT count(*) n, sum(size) bytes, sum(kind='audio') audio, sum(kind='cue') cue, "
        f"sum(kind='lrc') lrc, sum(kind='image') image, sum(kind='other') other, "
        f"sum(kind='audio' AND file_sha256 IS NOT NULL) hashed "
        f"FROM file f WHERE f.removed_at IS NULL {w}", a).fetchone()
    s = dict(row)
    s["by_codec"] = {r["codec"] or "?": r["n"] for r in con.execute(
        f"SELECT codec, count(*) n FROM file f WHERE f.removed_at IS NULL AND kind='audio' {w} "
        f"GROUP BY codec ORDER BY n DESC", a)}
    s["filters"] = {}
    for name in FILTERS:
        rows = run(con, name, root_id)
        # garbled_tags yields one row per field; count files, as the UI lists files
        s["filters"][name] = len({r["relpath"] for r in rows}) if name == "garbled_tags" else len(rows)
    return s
