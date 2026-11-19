"""The ONLY module that imports beets (pinned 2.14.1). See m0/EVIDENCE_REPORT.md.

Lessons from M0 applied here:
  * beets keeps ~10 pieces of process-global state; we reset all of them, AND the
    caller runs every match in a fresh worker process (belt and braces)
  * Library(':memory:') writes migration backups unless create_backup_before_migrations
    is off -- we never construct a Library here at all
  * the AcoustID lookup key is the module constant beetsplug.chroma.API_KEY
  * user config (~/.config/beets) is never read: BEETSDIR -> empty temp dir, user=False
"""
from __future__ import annotations

import os
import sys
import tempfile

_EMPTY = tempfile.mkdtemp(prefix="mlt_beetsdir_")
os.environ["BEETSDIR"] = _EMPTY
sys.dont_write_bytecode = True

from beets import config as bconfig  # noqa: E402
from beets import logging as blogging  # noqa: E402
from beets import metadata_plugins, plugins  # noqa: E402
from beets.autotag import Source, tag_album, tag_item  # noqa: E402
from beets.library import Item  # noqa: E402
from beets.util import cached_classproperty  # noqa: E402

from ..tags import repair_latin1  # noqa: E402

BEETS_DEFAULT_APP_KEY = "1vOwZtEn"
REPAIR_FIELDS = ("title", "artist", "album", "albumartist")
# Ranking: beets sorts by distance only. Fingerprints can confirm MusicBrainz recordings
# only, so a Discogs/Deezer copy of the same release ties on distance but shows 0 agreement.
# Rank by distance minus a small bonus for the share of fingerprints that agree.
FP_RANK_BONUS = 0.05
SCRIPT_RETRY_DISTANCE = 0.15   # search again in the other Chinese script if nothing is this close
# Measured on the NAS APE sample (40 albums): the second search returned the SAME candidates
# every time -- the services already fold Simplified/Traditional -- while tripling the time.
# Off by default; MLT_SCRIPT_RETRY=1 turns it on.
SCRIPT_RETRY = os.environ.get("MLT_SCRIPT_RETRY") == "1"
from .results import CUE_FP_TRACKS  # noqa: E402


def _rerank(cands: list[dict], n_files: int) -> list[dict]:
    key = lambda c: c["distance"] - FP_RANK_BONUS * (c["fingerprint_agrees"] / max(1, n_files))
    cands = sorted(cands, key=key)
    for i, c in enumerate(cands):
        c["rank"] = i
    return cands


def _repair(items) -> int:
    """Apply GBK/Big5 mojibake repairs to the in-memory items beets matches with.
    Files are never written. Returns how many items had at least one field repaired."""
    n = 0
    for it in items:
        changed = False
        for f in REPAIR_FIELDS:
            v = it.get(f)
            if isinstance(v, str) and v:
                r = repair_latin1(v)
                if r:
                    it[f] = r["proposal"]
                    changed = True
        n += changed
    return n
_LOG = blogging.getLogger("beets")


def _reset():
    bconfig.clear()
    bconfig.read(user=False, defaults=True)
    plugins._instances.clear()
    plugins.BeetsPlugin.listeners.clear()
    plugins.BeetsPlugin._raw_listeners.clear()
    for attr in ("template_funcs", "template_fields", "album_template_fields"):
        getattr(plugins.BeetsPluginMeta, attr).clear()
    metadata_plugins.find_metadata_source_plugins.cache_clear()
    metadata_plugins.get_metadata_source.cache_clear()
    metadata_plugins.get_penalty.cache_clear()
    cached_classproperty.cache.clear()
    ch = sys.modules.get("beetsplug.chroma")
    if ch:
        ch._matches.clear()
        ch._fingerprints.clear()
        ch._acoustids.clear()
        ch.API_KEY = BEETS_DEFAULT_APP_KEY


def sources() -> list[str]:
    """Candidate sources: MusicBrainz always; Deezer unless MLT_SOURCES excludes it;
    Discogs only when a personal token is in DISCOGS_TOKEN."""
    wanted = os.environ.get("MLT_SOURCES", "musicbrainz,deezer,discogs").lower().split(",")
    out = ["musicbrainz"]
    if "deezer" in wanted:
        out.append("deezer")
    if "discogs" in wanted and os.environ.get("DISCOGS_TOKEN"):
        out.append("discogs")
    return out


MAX_COMPARE = 300   # characters; real titles are far shorter


def _bound_string_distance():
    """Some catalogue entries are junk: Discogs has a joke release "Unicode" whose track titles
    are 274,000 characters (every code point). beets transliterates both strings on every
    comparison, so one such candidate made a 21-track album take 13 s of CPU. Compare at most
    MAX_COMPARE characters; any real title is unaffected."""
    import importlib
    D = importlib.import_module("beets.autotag.distance")   # the package re-exports a function of that name
    if getattr(D._string_dist_basic, "_mlt_bounded", False):
        return
    orig = D._string_dist_basic

    def bounded(a: str, b: str) -> float:
        return orig(a[:MAX_COMPARE], b[:MAX_COMPARE])
    bounded._mlt_bounded = True
    D._string_dist_basic = bounded


def configure(acoustid_key: str | None, search_limit: int = 5):
    from . import httpcache
    httpcache.install()          # web lookups cached on disk; Deezer/Discogs throttled
    _bound_string_distance()
    _reset()
    use_chroma = bool(acoustid_key)
    srcs = sources()
    bconfig["plugins"] = srcs + (["chroma"] if use_chroma else [])
    if "discogs" in srcs:
        bconfig["discogs"]["user_token"] = os.environ["DISCOGS_TOKEN"]
        bconfig["discogs"]["user_token"].redact = True
    for s in srcs:
        bconfig[s]["search_limit"] = search_limit
    bconfig["create_backup_before_migrations"] = False
    bconfig["import"]["write"] = False
    plugins.load_plugins()
    if use_chroma:
        sys.modules["beetsplug.chroma"].API_KEY = acoustid_key
    return use_chroma


def _other_script_search(source, search: dict) -> dict | None:
    """Search terms in the other Chinese script, if converting changes them."""
    from ..chinese import other_script
    artist = search.get("artist") or source.artist
    name = search.get("album") or search.get("title") or source.name
    a2, n2 = other_script(artist), other_script(name)
    if not (a2 or n2):
        return None
    return {"artist": a2 or artist, "name": n2 or name}


def _merge(first: list, second: list) -> list:
    """Merge candidate lists from two searches; keep each release once (lowest distance)."""
    best = {}
    for c in list(first) + list(second):
        k = (c.info.data_source, c.info.identifier if hasattr(c.info, "identifier") else id(c))
        if k not in best or c.distance.distance < best[k].distance.distance:
            best[k] = c
    return sorted(best.values(), key=lambda c: c.distance.distance)


def _track(ti, dist, path=None) -> dict:
    td = dist.tracks.get(ti) if dist is not None else None
    return {"path": path, "recording_id": ti.track_id, "release_track_id": ti.release_track_id,
            "index": ti.index, "medium": ti.medium, "medium_index": ti.medium_index,
            "title": ti.title, "artist": ti.artist, "length": ti.length,
            "track_distance": float(td.distance) if td is not None else None}


def _fingerprint(items, use_chroma) -> dict[str, bool]:
    """Fingerprint each file; returns {path: has a MusicBrainz-linked AcoustID match}."""
    if not use_chroma:
        return {}
    from . import httpcache
    ch = sys.modules["beetsplug.chroma"]
    for it in items:
        p = os.fsdecode(it.path)
        httpcache.fp_keys[p] = httpcache.file_key(p)
        ch.acoustid_match(_LOG, it.path)
    return {os.fsdecode(it.path): it.path in ch._matches for it in items}


def match_album(paths: list[str], acoustid_key: str | None, search: dict | None = None) -> dict:
    """search: optional {"artist", "album"} terms or {"ids": [release mbid]} from the user."""
    search = search or {}
    use_chroma = configure(acoustid_key)
    items = [Item.from_path(p) for p in paths]
    repaired = _repair(items)
    linked = _fingerprint(items, use_chroma)
    source = Source.from_items(items)
    proposal = tag_album(source, search_artist=search.get("artist") or None,
                         search_name=search.get("album") or None, search_ids=search.get("ids") or [])
    found = list(proposal.candidates)
    variant = None
    if SCRIPT_RETRY and not search.get("ids") and (not found or found[0].distance.distance > SCRIPT_RETRY_DISTANCE):
        variant = _other_script_search(source, search)
        if variant:
            found = _merge(found, tag_album(source, search_artist=variant["artist"], search_name=variant["name"]).candidates)
    cands = []
    for rank, c in enumerate(found):
        info = c.info
        mapping = [_track(ti, c.distance, os.fsdecode(it.path)) for it, ti in c.mapping.items()]
        rec_ids = {m["recording_id"] for m in mapping}
        n_fp = sum(1 for it, ti in c.mapping.items()
                   if os.fsdecode(it.path) in linked and linked[os.fsdecode(it.path)]
                   and ti.track_id in _recordings(it))
        cands.append({
            "rank": rank, "source": info.data_source, "album_id": info.album_id,
            "album": info.album, "artist": info.artist, "year": info.year, "country": info.country,
            "label": info.label, "media": info.media, "mediums": info.mediums,
            "distance": float(c.distance.distance),
            "penalties": sorted((k, round(float(v), 6)) for k, v in c.distance.items() if v),
            "extra_items": [os.fsdecode(i.path) for i in c.extra_items],
            "extra_tracks": [{"title": t.title, "index": t.index, "recording_id": t.track_id}
                             for t in c.extra_tracks],
            "fingerprint_agrees": n_fp, "n_mapped": len(mapping), "recording_ids": len(rec_ids),
            "tracks": mapping,
        })
    cands = _rerank(cands, len(items))
    _reset()
    return {"kind": "album", "recommendation": proposal.recommendation.name, "acoustid_used": use_chroma,
            "files_fingerprint_linked": sum(linked.values()), "repaired": repaired, "candidates": cands,
            "script_variant": variant}


def match_track(path: str, acoustid_key: str | None, search: dict | None = None) -> dict:
    search = search or {}
    use_chroma = configure(acoustid_key)
    it = Item.from_path(path)
    repaired = _repair([it])
    linked = _fingerprint([it], use_chroma)
    proposal = tag_item(Source.from_item(it), search_artist=search.get("artist") or None,
                        search_name=search.get("title") or None, search_ids=search.get("ids") or None)
    cands = []
    for rank, c in enumerate(proposal.candidates):
        ti = c.info
        cands.append({
            "rank": rank, "source": ti.data_source, "album_id": None, "album": None,
            "artist": ti.artist, "year": None, "country": None, "label": None, "media": None,
            "mediums": None, "distance": float(c.distance.distance),
            "penalties": sorted((k, round(float(v), 6)) for k, v in c.distance.items() if v),
            "extra_items": [], "extra_tracks": [],
            "fingerprint_agrees": int(bool(linked.get(path)) and ti.track_id in _recordings(it)),
            "n_mapped": 1, "recording_ids": 1,
            "tracks": [{**_track(ti, None, path), "track_distance": float(c.distance.distance)}],
        })
    cands = _rerank(cands, 1)
    _reset()
    return {"kind": "track", "recommendation": proposal.recommendation.name, "acoustid_used": use_chroma,
            "files_fingerprint_linked": sum(linked.values()), "repaired": repaired, "candidates": cands}


def _recordings(item) -> set:
    ch = sys.modules.get("beetsplug.chroma")
    if not ch or item.path not in ch._matches:
        return set()
    return set(ch._matches[item.path][0])


def _segment_wav(src: str, start: float, length: float | None, out: str, offset: int = 0) -> bool:
    """Decode up to 2 minutes of one cue track to a temp WAV for fingerprinting."""
    import subprocess
    dur = min(length or 120.0, 120.0)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if offset:
        cmd += ["-skip_initial_bytes", str(offset)]
    cmd += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src, "-map", "0:a:0", "-ac", "2", out]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def match_cue(audio_path: str, album: dict, tracks: list[dict], acoustid_key: str | None,
              search: dict | None = None, offset: int = 0) -> dict:
    """Match a cue-sheet image album: one long audio file + a cue listing its tracks.

    album:  {"title", "performer", "date"} from the cue
    tracks: [{"number", "title", "performer", "start", "length"}]
    Each track becomes an in-memory beets item (cue title + length); when an AcoustID key is
    available, each track is fingerprinted from its own slice of the image (first 2 minutes).
    """
    import shutil as _sh
    from . import httpcache
    search = search or {}
    use_chroma = configure(acoustid_key)
    tmp = tempfile.mkdtemp(prefix="mlt_cue_")
    # Decoding slices of a big image over the network is most of the time, and for this
    # collection fingerprints rarely link (APE sample: 1 of 40). Fingerprint at most
    # CUE_FP_TRACKS evenly spaced tracks; the others match on title and length.
    step = max(1, len(tracks) // CUE_FP_TRACKS)
    fp_numbers = {t["number"] for t in tracks[::step][:CUE_FP_TRACKS]}
    try:
        items, number_of = [], {}
        for t in tracks:
            it = Item(title=t.get("title") or "", artist=t.get("performer") or album.get("performer") or "",
                      album=album.get("title") or "", albumartist=album.get("performer") or "",
                      track=t["number"], tracktotal=len(tracks), length=t.get("length") or 0.0)
            seg = os.path.join(tmp, f"t{t['number']:02d}.wav")
            fp_key = httpcache.file_key(audio_path, f"{t['start']:.3f}:{t.get('length')}:{offset}")
            httpcache.fp_keys[seg] = fp_key
            if use_chroma and t["number"] in fp_numbers and (
                    httpcache.cached_fingerprint(fp_key) or      # known slice: no need to decode it again
                    _segment_wav(audio_path, t["start"], t.get("length"), seg, offset)):
                it.path = os.fsencode(seg)
            else:
                it.path = os.fsencode(os.path.join(tmp, f"virtual-{t['number']:02d}"))
            number_of[it.path] = t["number"]
            items.append(it)
        repaired = _repair(items)   # cue text can be legacy-encoded too (decoded already, rarely garbled)
        linked = {}
        if use_chroma:
            ch = sys.modules["beetsplug.chroma"]
            for it in items:
                p = os.fsdecode(it.path)
                if os.path.exists(it.path) or httpcache.cached_fingerprint(httpcache.fp_keys.get(p)):
                    ch.acoustid_match(_LOG, it.path)
            linked = {it.path: it.path in ch._matches for it in items}
        source = Source.from_items(items)
        proposal = tag_album(source, search_artist=search.get("artist") or None,
                             search_name=search.get("album") or None, search_ids=search.get("ids") or [])
        found = list(proposal.candidates)
        variant = None
        if SCRIPT_RETRY and not search.get("ids") and (not found or found[0].distance.distance > SCRIPT_RETRY_DISTANCE):
            variant = _other_script_search(source, search)
            if variant:
                found = _merge(found, tag_album(source, search_artist=variant["artist"],
                                                search_name=variant["name"]).candidates)
        cands = []
        for rank, c in enumerate(found):
            info = c.info
            mapping = []
            fp = 0
            for it, ti in c.mapping.items():
                m = _track(ti, c.distance)
                m.pop("path")
                m["cue_track"] = number_of[it.path]
                m["cue_title"] = it.title
                mapping.append(m)
                if linked.get(it.path) and ti.track_id in _recordings(it):
                    fp += 1
            cands.append({
                "rank": rank, "source": info.data_source, "album_id": info.album_id,
                "album": info.album, "artist": info.artist, "year": info.year, "country": info.country,
                "label": info.label, "media": info.media, "mediums": info.mediums,
                "distance": float(c.distance.distance),
                "penalties": sorted((k, round(float(v), 6)) for k, v in c.distance.items() if v),
                "extra_items": [], "extra_tracks": [{"title": t.title, "index": t.index, "recording_id": t.track_id}
                                                    for t in c.extra_tracks],
                "fingerprint_agrees": fp, "n_mapped": len(mapping), "recording_ids": len(mapping),
                "tracks": sorted(mapping, key=lambda m: m["cue_track"]),
            })
        cands = _rerank(cands, len(items))
        return {"kind": "cue", "recommendation": proposal.recommendation.name, "acoustid_used": use_chroma,
                "files_fingerprint_linked": sum(linked.values()), "repaired": repaired, "candidates": cands,
                "n_tracks": len(items), "script_variant": variant}
    finally:
        _reset()
        _sh.rmtree(tmp, ignore_errors=True)
