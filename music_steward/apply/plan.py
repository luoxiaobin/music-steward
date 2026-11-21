"""Build a frozen plan from accepted review decisions.

Nothing on disk changes here. Each item records the source's size + mtime at plan time
(apply refuses a source that changed since), the destination path from the naming
rules, and the tag values to write to the COPY. Conflicts are flagged, never resolved
by overwriting: existing destination, two items on the same name (case/NFC-insensitive),
or a source that changed since it was scanned.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from .naming import album_dir, collision_key, safe_component, track_name

COVER = re.compile(r"^(cover|folder|front|albumart)([ _-]?(large|front))?\.(jpe?g|png)$", re.I)


def _tags_for(c, t, kind) -> dict:
    total_discs = c["mediums"] or 1
    is_mb = (c.get("source") or "MusicBrainz") == "MusicBrainz"   # never put Deezer/Discogs ids in MB tags
    return {
        "title": t["title"], "artist": t["artist"] or c["artist"],
        "album": c["album"] if kind == "album" else None,
        "albumartist": c["artist"] if kind == "album" else None,
        "track": t["medium_index"] or t["track_index"] if kind == "album" else None,
        "disc": f"{t['medium']}/{total_discs}" if kind == "album" and total_discs > 1 else None,
        "date": c["year"], "mb_trackid": t["recording_id"] if is_mb else None,
        "mb_albumid": c["album_id"] if is_mb else None,
    }


def _cue_items(con, u, c, adir, root_path, items, new_stem_of, cue_titles=None):
    """A cue-sheet image album: copy the image (album-level tags) and a rewritten cue sheet
    (new FILE name, album + track titles from the release, original encoding). No splitting."""
    from .. import cue as cuemod
    from .naming import ext_for
    f = con.execute("SELECT * FROM file WHERE id=?", (json.loads(u["file_ids"])[0],)).fetchone()
    cf = con.execute("SELECT * FROM file WHERE id=?", (u["cue_file_id"],)).fetchone()
    multi = (c["mediums"] or 1) > 1 and c.get("_disc") is not None
    stem = safe_component(f"{c['album']} (CD {c['_disc']})" if multi else c["album"], "Album")
    image_name = stem + ext_for(f["codec"], f["ext"])
    is_mb = (c.get("source") or "MusicBrainz") == "MusicBrainz"
    items.append({"unit_id": u["id"], "file_id": f["id"], "kind": "audio",
                  "src": os.path.join(root_path, f["fs_relpath"]), "dest_rel": f"{adir}/{image_name}",
                  "codec": f["codec"], "size": f["size"], "mtime": f["mtime_ns"],
                  "tags": {"album": c["album"], "albumartist": c["artist"], "date": c["year"],
                           "title": c["album"],   # not a leftover first-track title from the source
                           "disc": f"{c['_disc']}/{c['mediums']}" if multi else None,
                           "mb_albumid": c["album_id"] if is_mb else None}})
    sheet = cuemod.parse(os.path.join(root_path, cf["fs_relpath"]))
    own = cuemod.file_for(sheet, f["relpath"].rsplit("/", 1)[-1])
    # a multi-image cue (one FILE per disc): this album's copy gets a cue of its own image only,
    # tracks numbered from 1 -- not the whole sheet with every FILE pointing at this one image
    keep = own.name if len([x for x in sheet.files if x.tracks]) > 1 else None
    tracks = {str(t["cue_track"]): {"title": (cue_titles or {}).get(str(t["cue_track"])) or t["title"],
                                    "performer": _script(c["artist"] if t["artist"] and t["artist"] == c.get("_artist_from")
                                                         else t["artist"], c.get("_script", "keep")) or c["artist"]}
              for t in con.execute("SELECT * FROM cand_cue_track WHERE candidate_id=?", (c["id"],))}
    items.append({"unit_id": u["id"], "file_id": cf["id"], "kind": "cue",
                  "src": os.path.join(root_path, cf["fs_relpath"]), "dest_rel": f"{adir}/{stem}.cue",
                  "codec": None, "size": cf["size"], "mtime": cf["mtime_ns"],
                  "tags": {"cue_rewrite": {"encoding": cue_encoding(con),
                                           "files": {own.name: image_name} if keep else
                                                    {fl.name: image_name for fl in sheet.files},
                                           "keep_file": keep,
                                           "album": {"title": c["album"], "performer": c["artist"]},
                                           "tracks": tracks}}})
    new_stem_of[f["relpath"].rsplit(".", 1)[0]] = f"{adir}/{stem}"


def album_cues(con=None) -> bool:
    """Write an album cue sheet for albums of separate track files too (user request after the
    trial: every album then opens the same way in a player). MLT_ALBUM_CUE=0 turns it off."""
    from .. import settings
    return settings.get(con, "album_cue") == "on"


def _q(s) -> str:
    return str(s or "").replace('"', "'").replace("\n", " ").strip()


def album_cue_text(c: dict, tracks: list[dict], disc: int | None) -> str:
    """A cue sheet over separate track files: one FILE + TRACK per file, each from 00:00:00."""
    lines = ["REM COMMENT \"Written by Music Steward\""]
    if c.get("year"):
        lines.append(f"REM DATE {c['year']}")
    if disc and (c.get("mediums") or 1) > 1:
        lines.append(f"REM DISCNUMBER {disc}")
        lines.append(f"REM TOTALDISCS {c['mediums']}")
    lines += [f'PERFORMER "{_q(c.get("artist"))}"', f'TITLE "{_q(c.get("album"))}"']
    for n, t in enumerate(tracks, 1):
        lines += [f'FILE "{_q(t["file"])}" WAVE', f"  TRACK {n:02d} AUDIO", f'    TITLE "{_q(t["title"])}"',
                  f'    PERFORMER "{_q(t["artist"] or c.get("artist"))}"', "    INDEX 01 00:00:00"]
    return "\r\n".join(lines) + "\r\n"


def cue_encoding(con=None) -> str | None:
    """Encoding of copied cue sheets: UTF-8 with a BOM by default (readable by foobar2000 on Mac
    and Windows and most players; GB18030 cues showed as mojibake on the Mac).
    MLT_CUE_ENCODING=original keeps each cue's own encoding (old Chinese Windows players)."""
    from .. import settings
    v = settings.get(con, "cue_encoding")
    return None if v == "original" else v


def script_rule(con=None) -> str:
    """How names are written in the copies (user decision 2026-11-19: Simplified, so one artist
    never ends up in two folders, 鄧麗君 / 邓丽君). MLT_SCRIPT=traditional|keep to change it."""
    from .. import settings
    return settings.get(con, "script")


def _script(s, rule):
    if not s or rule == "keep":
        return s
    from ..chinese import to_simplified, to_traditional
    return to_simplified(s) if rule == "simplified" else to_traditional(s)


_UNKNOWN_ARTIST = {"[unknown]", "unknown", "unknown artist", "[anonymous]", "anonymous", "none", "-"}


def _own_artist(con, u) -> str | None:
    """The artist the album's own cue sheet / tags / folder name gives (None if none or unusable)."""
    from ..recognize import local
    try:
        a = local.describe(con, u["id"])["artist"]
    except (OSError, ValueError, KeyError):
        return None
    return None if not a or a.casefold() in ("various artists", "various", "群星", "未知艺术家") else a


def _prefer_own_artist(con, u, c, tracks) -> None:
    """One artist, one name (user review 2026-11-19: 林子祥's albums went to '林子祥' and, from
    Discogs, to 'George Lam'). When the catalogue artist is junk ('[unknown]', 'Yu Hon Mei =')
    or written only in Latin letters while the album's own info names the artist in Chinese,
    use the album's own name -- for the folder, the tags and the per-track performers."""
    import re
    from ..chinese import has_cjk
    cat = re.sub(r"\s*=\s*$", "", (c["artist"] or "").strip()) or None
    junk = not cat or cat.casefold() in _UNKNOWN_ARTIST
    if not junk and has_cjk(cat):
        c["artist"] = cat
        return
    own = _own_artist(con, u)
    if own and (junk or (has_cjk(own) and cat.casefold() not in ("various artists", "various"))):
        for t in tracks.values():
            if not t.get("artist") or t["artist"] == c["artist"]:
                t["artist"] = own
        c["_artist_from"] = c["artist"]
        c["artist"] = own
    else:
        c["artist"] = None if junk else cat     # '[unknown]' with nothing better -> 'Unknown Artist'


class PlanError(Exception):
    pass


def check_destination(con: sqlite3.Connection, dest: Path):
    """The destination must never be inside a source root: sources are read-only."""
    if not dest.is_absolute():
        raise PlanError("destination must be an absolute path")
    d = os.path.realpath(dest)
    for (r,) in con.execute("SELECT path FROM root"):
        rr = os.path.realpath(r)
        if d == rr or d.startswith(rr.rstrip("/") + "/") or rr.startswith(d.rstrip("/") + "/"):
            raise PlanError(f"destination overlaps the source root {r}; choose a folder outside your collection")


def build(con: sqlite3.Connection, dest_root: str, root_id: int, scope: str = "") -> int:
    dest = Path(dest_root).expanduser().resolve()
    check_destination(con, dest)
    q = ("SELECT u.*, d.candidate_id, d.overrides_json FROM unit u JOIN decision d ON d.unit_id=u.id "
         "WHERE d.action='accept' AND u.root_id=?")
    args: tuple = (root_id,)
    if scope:
        q += " AND (u.folder=? OR (u.folder >= ? AND u.folder < ?))"
        args += (scope, scope + "/", scope + "/￿")
    root_path = con.execute("SELECT path FROM root WHERE id=?", (root_id,)).fetchone()["path"]
    items, prepared, notes = [], [], []
    for u in con.execute(q, args).fetchall():
        c = dict(con.execute("SELECT * FROM candidate WHERE id=?", (u["candidate_id"],)).fetchone())
        tracks = {t["file_id"]: dict(t) for t in con.execute("SELECT * FROM cand_track WHERE candidate_id=?", (c["id"],))}
        ov = json.loads(u["overrides_json"] or "{}")
        before = {"album": c["album"], "artist": c["artist"], "year": c["year"]}
        # the album's first release year, not a reissue's (user decision 2026-11-19)
        from .. import settings as _settings
        if _settings.get(con, "year") == "original" and c.get("original_year") and \
                (not c["year"] or c["original_year"] < c["year"]):
            c["year"] = c["original_year"]
        if (c.get("source") or "") != "Local":
            _prefer_own_artist(con, u, c, tracks)
        if c.get("_artist_from"):
            notes.append({"unit_id": u["id"], "label": u["label"], "type": "artist_merged",
                          "from": c["_artist_from"], "to": c["artist"]})
        rule = script_rule(con)   # catalogue/own-info names only; the user's own edits stay as typed
        pre = (c["album"], c["artist"])
        c["album"], c["artist"] = _script(c["album"], rule), _script(c["artist"], rule)
        if (c["album"], c["artist"]) != pre:
            notes.append({"unit_id": u["id"], "label": u["label"], "type": "script",
                          "from": " / ".join(x for x in pre if x), "to": " / ".join(x for x in (c["album"], c["artist"]) if x)})
        if before["year"] and c["year"] != before["year"]:
            notes.append({"unit_id": u["id"], "label": u["label"], "type": "original_year",
                          "from": before["year"], "to": c["year"]})
        c["_script"] = rule
        for t in tracks.values():
            t["artist"] = _script(t["artist"], rule)
        for k_from, k_to in (("album", "album"), ("albumartist", "artist"), ("year", "year")):
            if ov.get(k_from) not in (None, ""):
                c[k_to] = int(ov[k_from]) if k_from == "year" else ov[k_from]
        for fid, title in (ov.get("titles") or {}).items():
            if int(fid) in tracks and title:
                tracks[int(fid)]["title"] = title
        adir = (album_dir(c["artist"], c["album"], c["year"]) if u["kind"] in ("album", "cue")
                else f"{safe_component(c['artist'], 'Unknown Artist')}/Singles")
        # which disc of a set this unit is: the user's edit, else what the release mapping says
        if ov.get("disc"):
            disc = int(ov["disc"])
            for t in tracks.values():
                t["medium"] = disc
        elif u["kind"] == "cue":
            media = {r[0] for r in con.execute("SELECT DISTINCT medium FROM cand_cue_track WHERE candidate_id=?",
                                               (c["id"],)) if r[0]}
            disc = media.pop() if len(media) == 1 else None
        else:
            media = {t["medium"] for t in tracks.values() if t["medium"]}
            disc = media.pop() if len(media) == 1 else None
        prepared.append((u, c, tracks, ov, adir, disc))
    # discs of one set: accepted units that land in the same album folder share it
    discs_in = defaultdict(int)
    for u, c, tracks, ov, adir, disc in prepared:
        if u["kind"] in ("album", "cue"):
            k = collision_key(adir)
            discs_in[k] = max(discs_in[k], disc or 1, int(ov.get("discs") or 0), c["mediums"] or 1)
    for u, c, tracks, ov, adir, disc in prepared:
        if u["kind"] in ("album", "cue"):
            c["mediums"] = discs_in[collision_key(adir)]
            c["_disc"] = disc            # None: this unit holds the whole set (or it is unknown)
        new_stem_of = {}
        if u["kind"] == "cue":
            _cue_items(con, u, c, adir, root_path, items, new_stem_of, ov.get("cue_titles") or {})
        for fid in ([] if u["kind"] == "cue" else json.loads(u["file_ids"])):
            f = con.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
            t = tracks.get(fid)
            if t is None:
                continue  # file not on the chosen release: stays out of the plan (reported)
            name = track_name(t["title"], t["medium_index"] or t["track_index"], t["medium"],
                              c["mediums"], f["codec"], f["ext"])
            new_stem_of[f["relpath"].rsplit(".", 1)[0]] = f"{adir}/{name.rsplit('.', 1)[0]}"
            items.append({"unit_id": u["id"], "file_id": fid, "kind": "audio",
                          "src": os.path.join(root_path, f["fs_relpath"]), "dest_rel": f"{adir}/{name}",
                          "codec": f["codec"], "tags": _tags_for(c, t, u["kind"]),
                          "size": f["size"], "mtime": f["mtime_ns"]})
        if u["kind"] == "album" and album_cues(con):
            mine = [it for it in items if it["unit_id"] == u["id"] and it["kind"] == "audio"]
            if len(mine) >= 2:
                mine.sort(key=lambda it: it["dest_rel"])
                multi = (c["mediums"] or 1) > 1 and c.get("_disc") is not None
                stem = safe_component(f"{c['album']} (CD {c['_disc']})" if multi else c["album"], "Album")
                text = album_cue_text(c, [{"file": it["dest_rel"].rsplit("/", 1)[-1], "title": it["tags"].get("title"),
                                           "artist": it["tags"].get("artist")} for it in mine], c.get("_disc"))
                items.append({"unit_id": u["id"], "file_id": mine[0]["file_id"], "kind": "cue_new",
                              "src": mine[0]["src"], "dest_rel": f"{adir}/{stem}.cue", "codec": None,
                              "tags": {"generated_text": text}, "size": mine[0]["size"], "mtime": mine[0]["mtime"]})
        # side files that live next to the unit's audio
        folders = {f.rsplit("/", 1)[0] if "/" in f else "" for f in new_stem_of}
        for folder in sorted(folders):  # deterministic: CD 1 before CD 2
            prefix = f"{folder}/" if folder else ""
            for s in con.execute("SELECT * FROM file WHERE root_id=? AND removed_at IS NULL AND kind IN ('lrc','image') "
                                 "AND relpath >= ? AND relpath < ?", (root_id, prefix, prefix + "￿")):
                if "/" in s["relpath"][len(prefix):]:
                    continue
                stem = s["relpath"].rsplit(".", 1)[0]
                if s["kind"] == "lrc" and stem in new_stem_of:
                    dest_rel = new_stem_of[stem] + ".lrc"
                elif s["kind"] == "image" and u["kind"] in ("album", "cue") and COVER.match(s["relpath"].rsplit("/", 1)[-1]):
                    dest_rel = f"{adir}/{safe_component(s['relpath'].rsplit('/', 1)[-1])}"
                else:
                    continue
                items.append({"unit_id": u["id"], "file_id": s["id"], "kind": s["kind"],
                              "src": os.path.join(root_path, s["fs_relpath"]), "dest_rel": dest_rel,
                              "codec": None, "tags": {}, "size": s["size"], "mtime": s["mtime_ns"]})
    items, dropped = _dedupe_images(items)
    dup_units = _identical_albums(items)
    # conflicts (an album skipped as an identical duplicate does not count)
    seen = defaultdict(list)
    for it in items:
        if it["unit_id"] not in dup_units:
            seen[collision_key(it["dest_rel"])].append(it)
    for it in items:
        it["status"], it["note"] = "ok", None
        if it["unit_id"] in dup_units:
            it["status"], it["note"] = "duplicate_source", f"identical to {dup_units[it['unit_id']]}: copied once"
            continue
        st = os.stat(it["src"]) if os.path.exists(it["src"]) else None
        if st is None or st.st_size != it["size"] or st.st_mtime_ns != it["mtime"]:
            it["status"], it["note"] = "source_changed", "source changed or missing since the scan; re-scan"
        elif (dest / it["dest_rel"]).exists():
            it["status"], it["note"] = "conflict_existing", "a file already exists at the destination"
        elif len(seen[collision_key(it["dest_rel"])]) > 1:
            it["status"], it["note"] = "conflict_duplicate", "another item in this plan has the same destination name"
    summary = {"items": len(items), "ok": sum(i["status"] == "ok" for i in items),
               "duplicate_images_skipped": dropped,
               "blocked": sum(i["status"] not in ("ok", "duplicate_source") for i in items),
               "duplicate_albums_skipped": len(dup_units),
               "bytes": sum(i["size"] for i in items if i["status"] == "ok"),
               "albums": len({i["unit_id"] for i in items}), "script": script_rule(con),
               "album_cues": sum(i["kind"] == "cue_new" and i["status"] == "ok" for i in items),
               "changes": {t: sum(n["type"] == t for n in notes) for t in ("artist_merged", "script", "original_year")},
               "notes": notes[:1000]}
    pid = con.execute("INSERT INTO plan(created_at, dest_root, root_id, scope, summary_json) VALUES(?,?,?,?,?)",
                      (time.time(), str(dest), root_id, scope, json.dumps(summary))).lastrowid
    con.executemany("INSERT INTO plan_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(pid, n, i["unit_id"], i["file_id"], i["kind"], i["src"], i["dest_rel"], i["codec"],
                      json.dumps(i["tags"], ensure_ascii=False), i["size"], i["mtime"], i["status"], i["note"])
                     for n, i in enumerate(items)])
    con.commit()
    return pid


def _identical_albums(items: list[dict]) -> dict[int, str]:
    """The same album twice in the collection (李烁 好梦留人 in two folders): when every audio
    file of one album lands on the same destination as another album's file AND is
    byte-identical to it, copy the album once. Returns {skipped unit_id: folder of the kept one}.
    Anything less than identical stays a visible conflict."""
    from ..hashing import file_sha256
    audio = defaultdict(list)
    for it in items:
        if it["kind"] == "audio":
            audio[it["unit_id"]].append(it)
    by_dest = defaultdict(list)
    for uid, its in audio.items():
        for it in its:
            by_dest[collision_key(it["dest_rel"])].append(it)
    skipped: dict[int, str] = {}
    for uid in sorted(audio):
        if uid in skipped:
            continue
        for other in sorted(audio):
            if other <= uid or other in skipped or len(audio[other]) != len(audio[uid]):
                continue
            pairs = []
            for it in audio[other]:
                mate = next((m for m in by_dest[collision_key(it["dest_rel"])] if m["unit_id"] == uid), None)
                if mate is None or mate["size"] != it["size"]:
                    break
                pairs.append((mate, it))
            else:
                if pairs and all(file_sha256(a["src"]) == file_sha256(b["src"]) for a, b in pairs):
                    skipped[other] = os.path.dirname(pairs[0][0]["src"])
    return skipped


def _dedupe_images(items: list[dict]) -> tuple[list[dict], int]:
    """Cover images from several disc folders (CD 1/Folder.jpg, CD 2/Folder.jpg) land on one
    name. Keep one copy when they are byte-identical; when they differ, keep both and name
    the later ones after their own folder ("Folder (CD 2).jpg"). Never a blocking conflict."""
    from ..hashing import file_sha256
    groups = defaultdict(list)
    for it in items:
        if it["kind"] == "image":
            groups[(it["unit_id"], collision_key(it["dest_rel"]))].append(it)
    drop = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        kept_hashes = {file_sha256(group[0]["src"]): group[0]}
        for it in group[1:]:
            h = file_sha256(it["src"])
            if h in kept_hashes:
                drop.add(id(it))
                continue
            kept_hashes[h] = it
            folder = os.path.basename(os.path.dirname(it["src"]))
            stem, ext = os.path.splitext(it["dest_rel"])
            it["dest_rel"] = f"{stem} ({safe_component(folder)}){ext}"
    return [i for i in items if id(i) not in drop], len(drop)


def load(con: sqlite3.Connection, plan_id: int) -> dict:
    p = dict(con.execute("SELECT * FROM plan WHERE id=?", (plan_id,)).fetchone())
    p["summary"] = json.loads(p.pop("summary_json"))
    p["items"] = [{**dict(r), "tags": json.loads(r["tags_json"] or "{}")} for r in con.execute(
        "SELECT * FROM plan_item WHERE plan_id=? ORDER BY seq", (plan_id,))]
    for it in p["items"]:
        it.pop("tags_json", None)
    p["runs"] = [dict(r) for r in con.execute("SELECT * FROM run WHERE plan_id=? ORDER BY id", (plan_id,))]
    return p
