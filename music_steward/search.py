"""Find albums, folders and tracks anywhere in the collection.

Matching is by any part of a name, ignoring case and Simplified/Traditional (邓丽君 finds 鄧麗君),
over repaired tag text (garbled GBK/Big5 tags are searchable by their real text), and by pinyin:
full ('denglijun', 'deng li jun') or initials ('dlj').

A small table `search_index` holds one normalized text per album / folder / track. It is rebuilt
when the collection or the recognition results change (checked on each query, cheap).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from functools import lru_cache

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_index(
    kind TEXT NOT NULL,          -- album | folder | track
    ref INTEGER,                 -- unit.id (album) / file.id (track)
    root_id INTEGER NOT NULL,
    folder TEXT NOT NULL,        -- album/track: its folder; folder: itself
    label TEXT NOT NULL,         -- what the result shows
    sub TEXT,                    -- second line
    n INTEGER,                   -- folder: tracks inside
    text TEXT NOT NULL           -- normalized search text
);
CREATE INDEX IF NOT EXISTS search_kind ON search_index(kind, root_id);
"""
_SPACE = re.compile(r"[\s\-_.·•,，、/\\|()（）\[\]【】《》<>'\"“”‘’!！?？:：~～+＋&]+")


@lru_cache(maxsize=None)
def _syl(ch: str) -> str:
    from unidecode import unidecode
    return unidecode(ch).strip().lower()


def _is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


def normalize(s: str | None) -> str:
    """Casefolded, Simplified, without spaces and punctuation."""
    from .chinese import to_simplified
    return _SPACE.sub("", to_simplified(s or "").casefold())


def pinyin(s: str | None) -> tuple[str, str]:
    """('denglijun', 'dlj') for 邓丽君 / 鄧麗君; latin letters and digits pass through."""
    full, init = [], []
    for ch in (s or ""):
        if _is_cjk(ch):
            y = _syl(ch)
            full.append(y)
            init.append(y[:1])
        elif ch.isalnum():
            full.append(ch.casefold())
            init.append(ch.casefold())
    return "".join(full), "".join(init)


def index_text(*parts: str | None) -> str:
    parts = [p for p in parts if p]
    norm = " ".join(normalize(p) for p in parts)
    py = [pinyin(p) for p in parts if any(_is_cjk(c) for c in p)]
    return " | ".join([norm] + [f for f, _ in py] + [i for _, i in py])


def _tags(row) -> dict:
    t = json.loads(row["tags_json"] or "{}")
    for i in json.loads(row["tag_issues_json"] or "[]"):
        if i.get("proposal") and i.get("field"):
            t[i["field"]] = i["proposal"]
    return t


def _signature(con: sqlite3.Connection) -> str:
    f = con.execute("SELECT COUNT(*), MAX(id), MAX(last_scan) FROM file WHERE removed_at IS NULL").fetchone()
    u = con.execute("SELECT COUNT(*), MAX(id), MAX(finished_at) FROM unit").fetchone()
    d = con.execute("SELECT COUNT(*), MAX(decided_at), SUM(LENGTH(COALESCE(overrides_json,''))) FROM decision").fetchone()
    return json.dumps([list(f), list(u), list(d)])


def rebuild(con: sqlite3.Connection) -> dict:
    t0 = time.time()
    con.executescript(SCHEMA)
    con.execute("DELETE FROM search_index")
    rows, folders = [], {}
    for f in con.execute("SELECT id, root_id, relpath, tags_json, tag_issues_json FROM file "
                         "WHERE removed_at IS NULL AND kind='audio'"):
        t = _tags(f)
        folder, _, name = f["relpath"].rpartition("/")
        title = t.get("title") or name.rsplit(".", 1)[0]
        sub = " · ".join(x for x in (t.get("artist"), t.get("album")) if x)
        rows.append(("track", f["id"], f["root_id"], folder, title, sub, None,
                     index_text(title, t.get("artist"), t.get("album"), name)))
        parts = folder.split("/") if folder else []
        for i in range(1, len(parts) + 1):            # every folder on the way counts its tracks
            k = (f["root_id"], "/".join(parts[:i]))
            folders[k] = folders.get(k, 0) + 1
    for (rid, folder), n in folders.items():
        leaf = folder.rsplit("/", 1)[-1]
        rows.append(("folder", None, rid, folder, leaf, folder, n, index_text(leaf)))
    for u in con.execute("SELECT u.id, u.root_id, u.folder, u.label, u.status, d.action, d.candidate_id, "
                         "d.overrides_json FROM unit u LEFT JOIN decision d ON d.unit_id=u.id"):
        c = con.execute("SELECT album, artist FROM candidate WHERE id=?", (u["candidate_id"],)).fetchone() \
            if u["candidate_id"] else con.execute("SELECT album, artist FROM candidate WHERE unit_id=? "
                                                  "ORDER BY rank LIMIT 1", (u["id"],)).fetchone()
        ov = json.loads(u["overrides_json"] or "{}")
        album, artist = ov.get("album") or (c["album"] if c else None), ov.get("albumartist") or (c["artist"] if c else None)
        status = {"accept": "Accepted", "skip": "Skipped"}.get(u["action"] or "", "")
        sub = " · ".join(x for x in (artist, album if album != u["label"] else None, status) if x)
        rows.append(("album", u["id"], u["root_id"], u["folder"], u["label"], sub, None,
                     index_text(u["label"], album, artist, u["folder"].rsplit("/", 1)[-1])))
    con.executemany("INSERT INTO search_index VALUES(?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO meta VALUES('search_signature', ?)", (_signature(con),))
    con.commit()
    return {"rows": len(rows), "seconds": round(time.time() - t0, 1)}


def ensure(con: sqlite3.Connection) -> bool:
    """Rebuild when the collection or recognition changed. Returns True if it rebuilt."""
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key='search_signature'").fetchone()
    if row and row[0] == _signature(con):
        return False
    rebuild(con)
    return True


def query(con: sqlite3.Connection, q: str, root_id: int | None = None, limit: int = 8) -> dict:
    q = (q or "").strip()
    out = {"q": q, "albums": [], "folders": [], "tracks": [], "counts": {}}
    if not q:
        return out
    ensure(con)
    needles = {normalize(q), re.sub(r"\s+", "", q.casefold())}   # names, or pinyin typed with spaces
    needles = [n for n in needles if n]
    if not needles:
        return out
    where = "(" + " OR ".join("text LIKE ?" for _ in needles) + ")"
    args = [f"%{n}%" for n in needles]
    if root_id:
        where += " AND root_id=?"
        args.append(root_id)
    hits = {"album": [], "folder": [], "track": []}
    for r in con.execute(f"SELECT kind, ref, root_id, folder, label, sub, n FROM search_index WHERE {where}", args):
        hits[r["kind"]].append(dict(r))       # one scan for all three groups

    def rank(r):
        lab = normalize(r["label"])
        py_full, py_init = pinyin(r["label"])
        in_name = any(n in lab or n in py_full or n == py_init for n in needles)
        starts = any(lab.startswith(n) or py_full.startswith(n) for n in needles)
        return (not starts, not in_name, -(r["n"] or 0), len(r["label"]))
    for kind, key in (("album", "albums"), ("folder", "folders"), ("track", "tracks")):
        out["counts"][key] = len(hits[kind])
        pool = hits[kind]
        if len(pool) > 5000:   # many hits: rank only those whose own name matches (text or pinyin)
            def named(h):
                lab, (full, init) = normalize(h["label"]), pinyin(h["label"])
                return any(n in lab or n in full or n in init for n in needles)
            pool = [h for h in pool if named(h)][:5000] or pool[:5000]
        best = sorted(pool, key=rank)
        out[key] = best[:limit]
    return out
