"""The trial set: a thin slice of the real collection taken through the whole journey (docs/TRIAL_E2E.md).

`pick` chooses about 20 albums from the app database -- reading nothing on disk -- so that together they cover the
hard cases: a clean well-known album, garbled Chinese tags, a mainland compilation, APE and FLAC images with cue
sheets, a multi-disc set, a folder of singles, surround (5.1/DTS) audio, and duets. It prefers albums nobody has
decided yet and small ones (a trial should take an evening, not a weekend). The set is saved in the app's own folder
(~/.music-steward/trial.json): it names the owner's albums, so it never goes into the repository.

`recognize --trial`, `plan --trial`, `trial show` and the album list's "Trial set" filter work on exactly these albums.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from . import db as dbmod
from .recognize.units import DISC_DIR, gone_units

COMPILATION = re.compile(r"精选|金曲|合集|经典|老歌|VOL|Vol|vol|\d+首|专辑|best|greatest|collection", re.I)
SINGLES = re.compile(r"单曲|單曲|singles?", re.I)
DUET = re.compile(r"\s(?:&|feat\.?|ft\.?|x)\s|[&/、;，,]|和|与|與", re.I)
SKIP = re.compile(r"(^|/)_mlt_scratch(/|$)|(^|/)_ORIGINALS(/|$)")

# (category, how many, what it tests) -- in the order they are filled
CATEGORIES = [
    ("well_known", 3, "a clean album with Latin-script tags: the happy path (Ready)"),
    ("garbled", 3, "GBK/Big5 tags shown as ÈÔÈ»…: repair and Simplified names"),
    ("compilation", 3, "a mainland compilation no catalogue knows: No match, then Own info"),
    ("cue_ape", 2, "an APE image with a cue sheet: split into tracks, cue rewrite"),
    ("cue_flac", 1, "a FLAC image with a cue sheet"),
    ("multi_disc", 2, "CD 1 / CD 2 folders: one album folder, disc numbers"),
    ("singles", 1, "a folder of single tracks: the singles rules"),
    ("surround", 2, "5.1 / DTS audio: odd formats"),
    ("duet", 2, "a track artist with two singers: each track's artist"),
]
TEXT = {c: t for c, _, t in CATEGORIES}
# filled rarest first: an album can stand for several kinds, and a common kind (a clean album) must not take the only
# multi-disc set or the only image there is
FILL_ORDER = ["cue_ape", "cue_flac", "multi_disc", "singles", "surround", "duet", "garbled", "compilation", "well_known"]


def path() -> Path:
    return Path(dbmod.DEFAULT_DB).parent / "trial.json"


def _files(con, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    return con.execute(f"SELECT id, relpath, codec, channels, tags_json, tag_issues_json FROM file WHERE id IN "
                       f"({','.join('?' * len(ids))})", ids).fetchall()


def _category_of(u: dict, files: list[sqlite3.Row]) -> list[str]:
    """Every category this album could stand for (an album can serve only one)."""
    tags = [json.loads(f["tags_json"] or "{}") for f in files]
    out = []
    if u["kind"] == "cue":
        codec = (files[0]["codec"] or "") if files else ""
        out.append("cue_ape" if codec == "ape" else "cue_flac" if codec == "flac" else None)
    if u["kind"] == "track":
        return ["singles"] if SINGLES.search(u["folder"]) else []
    n = len(files)
    if u["kind"] != "cue" and any(f["tag_issues_json"] for f in files):     # an image is already an image's case
        out.append("garbled")
    if any(DISC_DIR.match(f["relpath"].rsplit("/", 2)[-2] if f["relpath"].count("/") >= 1 else "") for f in files):
        out.append("multi_disc")
    if any((f["channels"] or 0) > 2 for f in files):
        out.append("surround")
    artists = {(t.get("artist") or "").strip() for t in tags}
    if any(DUET.search(a) for a in artists if a) and len(artists) > 1:
        out.append("duet")
    if n >= 10 and (COMPILATION.search(u["label"] or "") or COMPILATION.search(u["folder"].rsplit("/", 1)[-1])):
        out.append("compilation")
    if 6 <= n <= 20 and not any(f["tag_issues_json"] for f in files) and all(
            t.get("title") and t.get("album") and t.get("artist") and t["artist"].isascii() for t in tags):
        out.append("well_known")
    return [c for c in out if c]


def pick(con: sqlite3.Connection, root_id: int) -> list[dict]:
    """About 20 albums covering the hard cases; reads the database only. [{category, unit_id, folder, label, n}]"""
    gone = gone_units(con, root_id)
    decided = {r[0] for r in con.execute("SELECT unit_id FROM decision")}
    rows = [dict(r) for r in con.execute("SELECT id, folder, label, kind, file_ids FROM unit WHERE root_id=? "
                                         "ORDER BY folder, label", (root_id,))]
    pools: dict[str, list] = {c: [] for c, _, _ in CATEGORIES}
    singles: dict[str, list] = {}
    for u in rows:
        if u["id"] in gone or u["id"] in decided or SKIP.search(u["folder"]):
            continue
        ids = json.loads(u["file_ids"])
        if u["kind"] == "track":                    # singles are judged per folder, below
            if SINGLES.search(u["folder"]):
                singles.setdefault(u["folder"], []).append(u)
            continue
        files = _files(con, ids)
        if u["kind"] != "cue" and len(files) < 4:
            continue
        for c in _category_of(u, files):
            pools[c].append((len(files), u["folder"], u))
    chosen, used, parents, names = [], set(), set(), set()
    want_of = {c: n for c, n, _ in CATEGORIES}
    for cat in FILL_ORDER:
        want = want_of[cat]
        if cat == "singles":
            folders = sorted((len(v), k) for k, v in singles.items() if 3 <= len(v) <= 15)
            for n, folder in folders[:want]:
                chosen.append({"category": cat, "unit_ids": [u["id"] for u in singles[folder]], "folder": folder,
                               "label": folder.rsplit("/", 1)[-1], "n": n})
            continue
        got = 0
        for n, folder, u in sorted(pools[cat], key=lambda x: (x[0], x[1])):
            parent = folder.rsplit("/", 1)[0]
            name = folder.rsplit("/", 1)[-1].casefold()
            # variety: not two of a kind from one folder, and never the same album twice -- a real collection held
            # exact copies of one album in two top-level folders, and the first version picked both as the two duets
            if u["id"] in used or (parent, cat) in parents or name in names:
                continue
            chosen.append({"category": cat, "unit_ids": [u["id"]], "folder": folder, "label": u["label"], "n": n})
            used.add(u["id"])
            parents.add((parent, cat))
            names.add(name)
            got += 1
            if got == want:
                break
    order = [c for c, _, _ in CATEGORIES]
    return sorted(chosen, key=lambda c: order.index(c["category"]))


def save(root_id: int, chosen: list[dict], where: Path | None = None) -> Path:
    p = where or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"root_id": root_id, "picked_at": time.time(), "albums": chosen},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(where: Path | None = None) -> dict | None:
    p = where or path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def unit_ids(where: Path | None = None) -> set[int]:
    t = load(where)
    return {i for a in (t or {}).get("albums", []) for i in a["unit_ids"]}


def status(con: sqlite3.Connection, where: Path | None = None) -> list[dict]:
    """Where each album of the trial stands: recognized? decided? copied?"""
    from .recognize import results
    from .report import _copy_state
    t = load(where)
    if not t:
        return []
    ids = unit_ids(where)
    us = {u["id"]: u for u in results.units(con, t["root_id"]) if u["id"] in ids}
    copied = _copy_state(con, t["root_id"])
    out = []
    for a in t["albums"]:
        parts = [us.get(i) for i in a["unit_ids"]]
        parts = [p for p in parts if p]
        state = ("gone from the app" if not parts else
                 "copied" if all(copied.get(p["id"]) == "copied" for p in parts) else
                 "accepted" if all(p["decision"] and p["decision"]["action"] == "accept" for p in parts) else
                 "skipped" if all(p["decision"] and p["decision"]["action"] == "skip" for p in parts) else
                 "decided in part" if any(p["decision"] for p in parts) else
                 "waiting: " + ", ".join(sorted({p["confidence"] for p in parts})))
        out.append({**a, "state": state, "accepted": [_accepted_as(con, p) for p in parts if p["decision"]
                                                      and p["decision"]["action"] == "accept"]})
    return out


def _accepted_as(con: sqlite3.Connection, u: dict) -> dict:
    """Under which name an album was accepted, and where the name came from -- the first trial's main finding was
    albums accepted under ANOTHER album's name, which "copied" alone does not show. Edit fields win, as in the plan."""
    d = u["decision"]
    c = con.execute("SELECT album, artist, year, source FROM candidate WHERE id=?", (d["candidate_id"],)).fetchone()
    ov = json.loads(d.get("overrides_json") or "{}")
    c = dict(c) if c else {"album": None, "artist": None, "year": None, "source": None}
    source = {"Local": "own info", None: "?"}.get(c["source"], c["source"])
    return {"album": ov.get("album") or c["album"], "artist": ov.get("albumartist") or c["artist"],
            "year": ov.get("year") or c["year"], "source": source + (", edited" if ov else ""),
            "check": _check(con, u["id"], d["candidate_id"])}


def _check(con: sqlite3.Connection, unit_id: int, candidate_id: int) -> str | None:
    """What the review's checks say now about the accepted release: "different album: …", "other artist" or None --
    so a trial can measure the checks against the decisions already made, without re-reviewing first."""
    from .recognize import results
    d = results.unit_detail(con, unit_id)
    c = next((c for c in (d or {}).get("candidates", []) if c["id"] == candidate_id), None)
    if not c:
        return None
    ev = c["evidence"]
    if c.get("source") == "Local":            # own info from the tags, which may name another disc than the folder
        return "own info names another volume than the folder" if ev["numbers_differ"] else None
    why = [w for w, on in (("another volume", ev["numbers_differ"]), ("another artist", ev["artist"] == "differs"),
                           ("another title", not ev["title_agrees"])) if on]
    if ev["different_album"]:
        return "different album: " + ", ".join(why)
    return "other artist" if ev["artist"] == "differs" else None
