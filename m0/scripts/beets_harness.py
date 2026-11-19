"""
Embedding harness for beets 2.14.1 used by the M0a v2 spike.

Everything the app needs from beets goes through `run_job()`:
  - explicit settings in, plain-data candidates out
  - beets' process-global state is reset before and after every job
  - an in-memory library (":memory:") for destination-path rendering
  - an audit hook records every file write / sqlite connect / subprocess
    beets performs during the job

Process-global state in beets 2.14.1 that leaks between jobs unless reset
(found by reading the source, proven by the negative control in m0a_v2.py):
  beets.config                                 global confuse config
  beets.plugins._instances                     load_plugins() is a no-op once filled
  BeetsPlugin.listeners / _raw_listeners       ClassVar dicts, keep old plugins' handlers
  BeetsPluginMeta.template_* dicts             ClassVar dicts
  metadata_plugins.find_metadata_source_plugins / get_metadata_source / get_penalty
                                               functools.cache over the plugin list
  cached_classproperty.cache                   e.g. Distance._weights from first config seen
  beetsplug.chroma._matches/_fingerprints/_acoustids
                                               per-path AcoustID results
  beetsplug.chroma.API_KEY                     module constant (application key)
"""
from __future__ import annotations

import os
import sys
import time

# Make BEETSDIR point at an empty, throwaway dir *before* beets is imported,
# so nothing can ever resolve ~/.config/beets.
_EMPTY_BEETSDIR = os.environ.setdefault(
    "BEETSDIR", os.path.join(os.environ.get("TMPDIR", "/tmp"), "m0a_empty_beetsdir")
)
os.makedirs(_EMPTY_BEETSDIR, exist_ok=True)

sys.dont_write_bytecode = True  # the interpreter's own .pyc writes are not beets'

import acoustid  # noqa: E402
import requests  # noqa: E402
import beets  # noqa: E402
from beets import config as bconfig  # noqa: E402
from beets import metadata_plugins, plugins  # noqa: E402
from beets.autotag import Source, tag_album, tag_item  # noqa: E402
from beets.library import Item, Library  # noqa: E402
from beets.util import cached_classproperty  # noqa: E402

from beets import logging as _blogging  # noqa: E402

_LOG = _blogging.getLogger("beets")
AUDIO_EXTS = (".mp3", ".flac", ".ape", ".m4a", ".ogg", ".wav")
BEETS_DEFAULT_APP_KEY = "1vOwZtEn"  # beetsplug.chroma.API_KEY as shipped

# ---------------------------------------------------------------- audit hook
_AUDIT = {"active": False, "events": []}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _audit(event, args):
    if not _AUDIT["active"]:
        return
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                return
            writes = (mode and any(c in mode for c in "wax+")) or (
                mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS
            )
            if writes:
                _AUDIT["events"].append(("open-write", os.fsdecode(path)))
        elif event == "sqlite3.connect":
            _AUDIT["events"].append(("sqlite3.connect", os.fsdecode(args[0])))
        elif event in ("os.mkdir", "os.rename", "os.replace", "os.remove",
                       "os.unlink", "os.rmdir", "os.link", "os.symlink",
                       "shutil.copyfile", "shutil.move", "os.truncate", "os.utime"):
            _AUDIT["events"].append((event, os.fsdecode(args[0]) if args else ""))
        elif event == "subprocess.Popen":
            _AUDIT["events"].append(("subprocess", " ".join(map(os.fsdecode, args[1]))[:200]))
    except Exception:  # never let auditing break the job
        pass


sys.addaudithook(_audit)

# ---------------------------------------------------------- acoustid tracing
ACOUSTID_TRACE: list[dict] = []
_orig_api_request = acoustid._api_request


def redact(key: str | None) -> str:
    if not key:
        return "<none>"
    return f"{key[:2]}…{key[-2:]} (len {len(key)})" if len(key) > 4 else "****"


def _traced_api_request(url, params, timeout=None):
    rec = {"url": url, "client_key": redact(params.get("client")),
           "duration_s": params.get("duration"),
           "fingerprint_chars": len(params.get("fingerprint") or ""),
           "meta": params.get("meta")}
    t0 = time.time()
    try:
        resp = _orig_api_request(url, params, timeout)
        rec["status"] = resp.get("status")
        rec["error"] = resp.get("error")
        results = resp.get("results") or []
        rec["n_results"] = len(results)
        rec["top_score"] = results[0].get("score") if results else None
        rec["top_recordings"] = [r.get("id") for r in (results[0].get("recordings") or [])][:3] if results else []
        return resp
    except Exception as e:
        rec["exception"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        rec["elapsed_s"] = round(time.time() - t0, 2)
        ACOUSTID_TRACE.append(rec)


acoustid._api_request = _traced_api_request


# ------------------------------------------------- HTTP record / replay
# MusicBrainz returns equally-scored releases in a different order/set on
# repeated searches, so comparing two live runs conflates "beets leaked
# state" with "the server answered differently". For the leak test every
# run goes through this cache: the first request is recorded, later ones
# (in this or a fresh process) replay byte-identical responses.
_HTTP = {"path": None, "cache": {}, "hits": 0, "misses": 0}
_orig_send = requests.adapters.HTTPAdapter.send


def http_cache(path: str | None):
    import json as _json
    _HTTP.update(path=path, hits=0, misses=0, cache={})
    if path and os.path.exists(path):
        with open(path) as fh:
            _HTTP["cache"] = _json.load(fh)


def _cached_send(self, request, **kw):
    if not _HTTP["path"]:
        return _orig_send(self, request, **kw)
    import base64, hashlib, json as _json
    from requests.structures import CaseInsensitiveDict
    body = request.body or b""
    body = body.encode() if isinstance(body, str) else body
    key = hashlib.sha256(f"{request.method} {request.url}\n".encode() + body).hexdigest()
    c = _HTTP["cache"]
    if key not in c:
        _HTTP["misses"] += 1
        r = _orig_send(self, request, **kw)
        c[key] = {"status": r.status_code, "reason": r.reason, "url": r.url,
                  "headers": {k: v for k, v in r.headers.items()
                              if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")},
                  "content": base64.b64encode(r.content).decode()}
        with open(_HTTP["path"], "w") as fh:
            _json.dump(c, fh)
    else:
        _HTTP["hits"] += 1
    e = c[key]
    resp = requests.Response()
    resp.status_code, resp.reason, resp.url = e["status"], e["reason"], e["url"]
    resp.headers = CaseInsensitiveDict(e["headers"])
    resp._content = base64.b64decode(e["content"])
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    resp.request = request
    return resp


requests.adapters.HTTPAdapter.send = _cached_send


# ----------------------------------------------------------- state handling
def naive_reset():
    """What the previous spike effectively relied on: re-read config only."""
    bconfig.clear()
    bconfig.read(user=False, defaults=True)


def full_reset():
    """Return beets' process-global state to 'freshly imported'."""
    naive_reset()
    plugins._instances.clear()
    plugins.BeetsPlugin.listeners.clear()
    plugins.BeetsPlugin._raw_listeners.clear()
    for attr in ("template_funcs", "template_fields", "album_template_fields"):
        getattr(plugins.BeetsPluginMeta, attr).clear()
    metadata_plugins.find_metadata_source_plugins.cache_clear()
    metadata_plugins.get_metadata_source.cache_clear()
    metadata_plugins.get_penalty.cache_clear()
    cached_classproperty.cache.clear()
    if "beetsplug.chroma" in sys.modules:
        ch = sys.modules["beetsplug.chroma"]
        ch._matches.clear(); ch._fingerprints.clear(); ch._acoustids.clear()
        ch.API_KEY = BEETS_DEFAULT_APP_KEY


def state_snapshot() -> dict:
    """Observable beets global state, for leak comparison."""
    from beets.autotag.distance import Distance
    ch = sys.modules.get("beetsplug.chroma")
    return {
        "loaded_plugins": sorted(type(p).__name__ for p in plugins.find_plugins()),
        "listeners": {str(k): len(v) for k, v in sorted(plugins.BeetsPlugin.listeners.items(), key=lambda kv: str(kv[0])) if v},
        "metadata_sources": sorted(p.data_source for p in metadata_plugins.find_metadata_source_plugins()),
        "strong_rec_thresh": bconfig["match"]["strong_rec_thresh"].get(),
        "preferred_countries": bconfig["match"]["preferred"]["countries"].get(),
        "mb_search_limit": bconfig["musicbrainz"]["search_limit"].get() if "search_limit" in bconfig["musicbrainz"].keys() else None,
        "distance_weight_album": Distance._weights.get("album"),
        "distance_weight_track_title": Distance._weights.get("track_title"),
        "chroma_cached_paths": len(ch._matches) + len(ch._fingerprints) if ch else 0,
        # only meaningful while the chroma plugin is loaded
        "chroma_api_key": redact(ch.API_KEY) if ch and any(
            type(p).__name__ == "AcoustidPlugin" for p in plugins.find_plugins()) else None,
    }


def configure(settings: dict, reset: str = "full"):
    (full_reset if reset == "full" else naive_reset)()
    bconfig["plugins"] = list(settings["plugins"])
    m = bconfig["match"]
    m["strong_rec_thresh"] = settings["strong_rec_thresh"]
    m["preferred"]["countries"] = list(settings.get("preferred_countries", []))
    for k, v in (settings.get("distance_weights") or {}).items():
        m["distance_weights"][k] = v
    bconfig["musicbrainz"]["search_limit"] = settings.get("mb_search_limit", 5)
    bconfig["paths"] = {"default": settings.get("path_template",
                        "$albumartist/$album%aunique{}/$disc-$track $title")}
    # beets 2.14.1 backs up the DB before each of its 9 schema migrations to
    # "<db path>-before-<table>-<migration>.bak" -- for ":memory:" that is a
    # real file in the CWD (11 x 53 KB). Must be disabled explicitly.
    bconfig["create_backup_before_migrations"] = settings.get("create_backup_before_migrations", False)
    bconfig["import"]["write"] = False
    bconfig["import"]["copy"] = False
    bconfig["import"]["move"] = False
    plugins.load_plugins()
    if "chroma" in settings["plugins"]:
        sys.modules["beetsplug.chroma"].API_KEY = settings["acoustid_key"]


def _tree(root: str) -> dict:
    """path -> (size, mtime_ns) for every file under root (missing root -> {})."""
    out = {}
    if not os.path.exists(root):
        return out
    for r, _, files in os.walk(root):
        for f in files:
            p = os.path.join(r, f)
            try:
                st = os.stat(p)
                out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out


# -------------------------------------------------------------- candidates
def _audio_files(d: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(d):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(AUDIO_EXTS) and not f.startswith("."):
                p = os.path.join(root, f)
                if os.path.isfile(p):  # never trust the extension (dirs named *.flac exist)
                    out.append(p)
    return out


def _track_record(item: Item, ti, dist_obj, rel_to: str) -> dict:
    tdist = dist_obj.tracks.get(ti) if dist_obj is not None else None
    return {
        "item_path": os.path.relpath(os.fsdecode(item.path), rel_to),
        "recording_id": ti.track_id,
        "release_track_id": ti.release_track_id,
        "track_index": ti.index,
        "medium": ti.medium,
        "medium_index": ti.medium_index,
        "title": ti.title,
        "track_distance": float(tdist.distance) if tdist is not None else None,
    }


def _album_candidates(proposal, rel_to, lib, render_top=True) -> list[dict]:
    out = []
    for rank, cand in enumerate(proposal.candidates):
        info = cand.info
        rec = {
            "rank": rank,
            "data_source": info.data_source,
            "album_id": info.album_id,
            "album": info.album,
            "artist": info.artist,
            "year": info.year,
            "country": info.country,
            "distance": float(cand.distance.distance),
            "penalties": sorted((k, round(float(v), 6)) for k, v in cand.distance.items() if v),
            "mapping": sorted((_track_record(i, ti, cand.distance, rel_to)
                               for i, ti in cand.mapping.items()),
                              key=lambda r: r["item_path"]),
            "extra_items": sorted(os.path.relpath(os.fsdecode(i.path), rel_to) for i in cand.extra_items),
            "extra_tracks": [t.track_id for t in cand.extra_tracks],
            "rendered_destinations": None,
        }
        if render_top and rank == 0:
            rec["rendered_destinations"] = _render_destinations(cand, lib, rel_to)
        out.append(rec)
    return out


def _render_destinations(cand, lib, rel_to) -> dict:
    """Apply the candidate to *copies* of the items, add them to the
    in-memory library and ask beets for destination paths. Nothing is
    written to the audio files (item.write() is never called)."""
    pairs = []
    for item, data in cand.merged_pairs:
        clone = Item.from_path(item.path)  # fresh in-memory object; file only read
        clone.update(data)
        pairs.append((item, clone))
    if not pairs:
        return {}
    album = lib.add_album([c for _, c in pairs])  # in-memory DB only
    cand.apply_album_metadata(album)
    album.store()
    rendered = {}
    for item, clone in pairs:
        clone.load()
        rendered[os.path.relpath(os.fsdecode(item.path), rel_to)] = os.path.relpath(
            os.fsdecode(clone.destination()), os.fsdecode(lib.directory))
    return dict(sorted(rendered.items()))


def run_job(label: str, settings: dict, albums: dict[str, str],
            singles: dict[str, str] | None = None, reset: str = "full",
            reset_after: bool = True) -> dict:
    """Run one job. albums: {key: dir}; singles: {key: dir} matched per file."""
    _AUDIT["events"].clear()
    albums = {k: os.path.abspath(v) for k, v in (albums or {}).items()}
    singles = {k: os.path.abspath(v) for k, v in (singles or {}).items()}
    # Run with an empty CWD so any relative-path write by beets is visible.
    job_cwd = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"m0a_cwd_{os.getpid()}_{label}")
    os.makedirs(job_cwd, exist_ok=True)
    watched = {"cwd": job_cwd, "BEETSDIR": _EMPTY_BEETSDIR,
               "~/.config/beets": os.path.expanduser("~/.config/beets"),
               **{f"source:{k}": v for k, v in {**(albums or {}), **(singles or {})}.items()}}
    before = {k: _tree(v) for k, v in watched.items()}
    old_cwd = os.getcwd()
    os.chdir(job_cwd)
    configure(settings, reset)
    snapshot_at_start = state_snapshot()
    lib = Library(":memory:", directory=settings.get("dest_root", "/DEST"), set_music_dir=False)
    results = {}
    _AUDIT["active"] = True
    try:
        for key, d in (albums or {}).items():
            items = [Item.from_path(p) for p in _audio_files(d)]
            entry = {"kind": "album", "n_items": len(items)}
            if "chroma" in settings["plugins"]:
                ch = sys.modules["beetsplug.chroma"]
                for it in items:
                    ch.acoustid_match(_LOG, it.path)
                entry["acoustid_matched_items"] = sum(1 for it in items if it.path in ch._matches)
            t0 = time.time()
            proposal = tag_album(Source.from_items(items)) if items else None
            entry["elapsed_s"] = round(time.time() - t0, 1)
            entry["recommendation"] = proposal.recommendation.name if proposal else "none"
            entry["candidates"] = _album_candidates(proposal, d, lib) if proposal else []
            results[key] = entry
        for key, d in (singles or {}).items():
            tracks = {}
            for p in _audio_files(d):
                it = Item.from_path(p)
                if "chroma" in settings["plugins"]:
                    ch = sys.modules["beetsplug.chroma"]
                    ch.acoustid_match(_LOG, it.path)
                prop = tag_item(Source.from_item(it))
                tracks[os.path.relpath(p, d)] = {
                    "recommendation": prop.recommendation.name,
                    "acoustid_matched": "chroma" in settings["plugins"] and it.path in sys.modules["beetsplug.chroma"]._matches,
                    "candidates": [{
                        "rank": r, "recording_id": c.info.track_id, "title": c.info.title,
                        "artist": c.info.artist, "distance": float(c.distance.distance)}
                        for r, c in enumerate(prop.candidates)],
                }
            results[key] = {"kind": "singles", "tracks": tracks}
    finally:
        _AUDIT["active"] = False
        lib._connection().close()
        os.chdir(old_cwd)
    after = {k: _tree(v) for k, v in watched.items()}
    fs_diff = {}
    for k in watched:
        added = sorted(set(after[k]) - set(before[k]))
        removed = sorted(set(before[k]) - set(after[k]))
        changed = sorted(p for p in set(after[k]) & set(before[k]) if after[k][p] != before[k][p])
        if added or removed or changed:
            fs_diff[k] = {"added": added, "removed": removed, "changed": changed}
    snapshot_at_end = state_snapshot()
    if reset_after:
        full_reset()  # leave nothing behind for the next job
    return {
        "label": label,
        "settings": {k: (redact(v) if k == "acoustid_key" else v) for k, v in settings.items()},
        "reset": reset,
        "state_at_start": snapshot_at_start,
        "state_at_end": snapshot_at_end,
        "fs_events": list(_AUDIT["events"]),
        "fs_diff": fs_diff,
        "results": results,
    }
