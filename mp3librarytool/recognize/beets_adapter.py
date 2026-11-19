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

BEETS_DEFAULT_APP_KEY = "1vOwZtEn"
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


def configure(acoustid_key: str | None, search_limit: int = 5):
    _reset()
    use_chroma = bool(acoustid_key)
    bconfig["plugins"] = ["musicbrainz"] + (["chroma"] if use_chroma else [])
    bconfig["create_backup_before_migrations"] = False
    bconfig["import"]["write"] = False
    bconfig["musicbrainz"]["search_limit"] = search_limit
    plugins.load_plugins()
    if use_chroma:
        sys.modules["beetsplug.chroma"].API_KEY = acoustid_key
    return use_chroma


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
    ch = sys.modules["beetsplug.chroma"]
    for it in items:
        ch.acoustid_match(_LOG, it.path)
    return {os.fsdecode(it.path): it.path in ch._matches for it in items}


def match_album(paths: list[str], acoustid_key: str | None) -> dict:
    use_chroma = configure(acoustid_key)
    items = [Item.from_path(p) for p in paths]
    linked = _fingerprint(items, use_chroma)
    proposal = tag_album(Source.from_items(items))
    cands = []
    for rank, c in enumerate(proposal.candidates):
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
    _reset()
    return {"kind": "album", "recommendation": proposal.recommendation.name, "acoustid_used": use_chroma,
            "files_fingerprint_linked": sum(linked.values()), "candidates": cands}


def match_track(path: str, acoustid_key: str | None) -> dict:
    use_chroma = configure(acoustid_key)
    it = Item.from_path(path)
    linked = _fingerprint([it], use_chroma)
    proposal = tag_item(Source.from_item(it))
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
    _reset()
    return {"kind": "track", "recommendation": proposal.recommendation.name, "acoustid_used": use_chroma,
            "files_fingerprint_linked": sum(linked.values()), "candidates": cands}


def _recordings(item) -> set:
    ch = sys.modules.get("beetsplug.chroma")
    if not ch or item.path not in ch._matches:
        return set()
    return set(ch._matches[item.path][0])
