#!/usr/bin/env python3
"""
M0a v2 — closes the gaps in the first M0a spike.

  1.1 two jobs with different settings, sequentially, IN ONE PROCESS;
      compared against a fresh-process reference run + a negative control
  1.2 in-memory library; every file write / sqlite connect / subprocess
      during each job is listed, plus before/after directory diffs
  1.3 candidates (incl. item->recording mapping + distances) saved to an
      app SQLite, reloaded, compared for exact equality
  1.4 traced AcoustID lookup (key redacted) with parsed response
  1.5 APE: no encoder available -> real APE from the NAS sample is used
  1.6 match quality on real files copied from m0/nas_sample

Run:  ./venv/bin/python -I scripts/m0a_v2.py
Writes results/m0a_result.json and results/m0a_app.sqlite
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import beets_harness as H  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")
NAS_SAMPLE = os.path.join(ROOT, "nas_sample")
REAL = os.path.join(ROOT, "real_scratch")
SCRATCH = os.path.join(ROOT, "scratch")
RESULTS = os.path.join(ROOT, "results")
APP_DB = os.path.join(RESULTS, "m0a_app.sqlite")

ENV_KEY = os.environ.get("ACOUSTID_API_KEY")
ACOUSTID_KEY = ENV_KEY or H.BEETS_DEFAULT_APP_KEY
KEY_SOURCE = "env ACOUSTID_API_KEY" if ENV_KEY else "beets built-in app key (ACOUSTID_API_KEY not set)"

SETTINGS = {
    "A": {"plugins": ["musicbrainz", "chroma"], "strong_rec_thresh": 0.04,
          "preferred_countries": ["CN"], "distance_weights": {"album": 9.0},
          "mb_search_limit": 5, "acoustid_key": ACOUSTID_KEY},
    "B": {"plugins": ["musicbrainz"], "strong_rec_thresh": 0.5,
          "preferred_countries": ["US"], "distance_weights": {},
          "mb_search_limit": 2},
}
LEAK_ALBUMS = {"01_clean_baseline": os.path.join(FIX, "01_clean_baseline"),
               "06_compilation": os.path.join(FIX, "06_compilation")}


def strip_volatile(job: dict) -> dict:
    j = copy.deepcopy(job)
    for unit in j["results"].values():
        unit.pop("elapsed_s", None)
    j.pop("fs_events", None)
    j.pop("label", None)
    j.pop("reset", None)
    return j


def dict_diff(a: dict, b: dict) -> dict:
    return {k: {"in_process": a.get(k), "fresh_process": b.get(k)}
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


def fresh_process_job(name: str) -> dict:
    out = os.path.join(SCRATCH, f"fresh_{name}.json")
    subprocess.run([sys.executable, "-I", __file__, "--fresh-job", name, "--out", out],
                   check=True, env=os.environ.copy())
    with open(out) as fh:
        return json.load(fh)


def json_roundtrip(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


# ------------------------------------------------------------ app SQLite
SCHEMA = """
CREATE TABLE job(job_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL);
CREATE TABLE unit(job_id TEXT, unit_key TEXT, kind TEXT, n_items INT,
                  recommendation TEXT, acoustid_matched_items INT,
                  PRIMARY KEY(job_id, unit_key));
CREATE TABLE candidate(cand_pk INTEGER PRIMARY KEY, job_id TEXT, unit_key TEXT,
                  rank INT, data_source TEXT, album_id TEXT, album TEXT, artist TEXT,
                  year INT, country TEXT, distance REAL, penalties_json TEXT,
                  extra_items_json TEXT, extra_tracks_json TEXT, rendered_json TEXT,
                  UNIQUE(job_id, unit_key, rank));
CREATE TABLE mapping(cand_pk INT REFERENCES candidate, item_path TEXT,
                  recording_id TEXT, release_track_id TEXT, track_index INT,
                  medium INT, medium_index INT, title TEXT, track_distance REAL,
                  PRIMARY KEY(cand_pk, item_path));
CREATE TABLE single_track(job_id TEXT, unit_key TEXT, item_path TEXT,
                  recommendation TEXT, acoustid_matched INT,
                  PRIMARY KEY(job_id, unit_key, item_path));
CREATE TABLE single_candidate(job_id TEXT, unit_key TEXT, item_path TEXT, rank INT,
                  recording_id TEXT, title TEXT, artist TEXT, distance REAL,
                  PRIMARY KEY(job_id, unit_key, item_path, rank));
"""


def save_jobs(db_path: str, jobs: dict[str, dict]):
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    with con:
        for job_id, job in jobs.items():
            con.execute("INSERT INTO job VALUES(?,?)", (job_id, json.dumps(job["settings"], sort_keys=True)))
            for uk, u in job["results"].items():
                if u["kind"] == "album":
                    con.execute("INSERT INTO unit VALUES(?,?,?,?,?,?)",
                                (job_id, uk, "album", u["n_items"], u["recommendation"],
                                 u.get("acoustid_matched_items")))
                    for c in u["candidates"]:
                        cur = con.execute(
                            "INSERT INTO candidate(job_id,unit_key,rank,data_source,album_id,album,artist,"
                            "year,country,distance,penalties_json,extra_items_json,extra_tracks_json,rendered_json)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (job_id, uk, c["rank"], c["data_source"], c["album_id"], c["album"], c["artist"],
                             c["year"], c["country"], c["distance"], json.dumps(c["penalties"]),
                             json.dumps(c["extra_items"]), json.dumps(c["extra_tracks"]),
                             json.dumps(c["rendered_destinations"], ensure_ascii=False)))
                        for m in c["mapping"]:
                            con.execute("INSERT INTO mapping VALUES(?,?,?,?,?,?,?,?,?)",
                                        (cur.lastrowid, m["item_path"], m["recording_id"], m["release_track_id"],
                                         m["track_index"], m["medium"], m["medium_index"], m["title"],
                                         m["track_distance"]))
                else:
                    con.execute("INSERT INTO unit VALUES(?,?,?,?,?,?)", (job_id, uk, "singles", None, None, None))
                    for path, t in u["tracks"].items():
                        con.execute("INSERT INTO single_track VALUES(?,?,?,?,?)",
                                    (job_id, uk, path, t["recommendation"], int(bool(t["acoustid_matched"]))))
                        for c in t["candidates"]:
                            con.execute("INSERT INTO single_candidate VALUES(?,?,?,?,?,?,?,?)",
                                        (job_id, uk, path, c["rank"], c["recording_id"], c["title"],
                                         c["artist"], c["distance"]))
    con.close()


def load_jobs(db_path: str) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    jobs = {}
    for j in con.execute("SELECT * FROM job ORDER BY job_id"):
        results = {}
        for u in con.execute("SELECT * FROM unit WHERE job_id=? ORDER BY unit_key", (j["job_id"],)):
            if u["kind"] == "album":
                cands = []
                for c in con.execute("SELECT * FROM candidate WHERE job_id=? AND unit_key=? ORDER BY rank",
                                     (j["job_id"], u["unit_key"])):
                    mapping = [{
                        "item_path": m["item_path"], "recording_id": m["recording_id"],
                        "release_track_id": m["release_track_id"], "track_index": m["track_index"],
                        "medium": m["medium"], "medium_index": m["medium_index"], "title": m["title"],
                        "track_distance": m["track_distance"]}
                        for m in con.execute("SELECT * FROM mapping WHERE cand_pk=? ORDER BY item_path",
                                             (c["cand_pk"],))]
                    cands.append({
                        "rank": c["rank"], "data_source": c["data_source"], "album_id": c["album_id"],
                        "album": c["album"], "artist": c["artist"], "year": c["year"], "country": c["country"],
                        "distance": c["distance"],
                        "penalties": [tuple(p) for p in json.loads(c["penalties_json"])],
                        "mapping": mapping,
                        "extra_items": json.loads(c["extra_items_json"]),
                        "extra_tracks": json.loads(c["extra_tracks_json"]),
                        "rendered_destinations": json.loads(c["rendered_json"]),
                    })
                results[u["unit_key"]] = {"kind": "album", "n_items": u["n_items"],
                                          "recommendation": u["recommendation"],
                                          "acoustid_matched_items": u["acoustid_matched_items"],
                                          "candidates": cands}
            else:
                tracks = {}
                for t in con.execute("SELECT * FROM single_track WHERE job_id=? AND unit_key=? ORDER BY item_path",
                                     (j["job_id"], u["unit_key"])):
                    tracks[t["item_path"]] = {
                        "recommendation": t["recommendation"], "acoustid_matched": bool(t["acoustid_matched"]),
                        "candidates": [{"rank": c["rank"], "recording_id": c["recording_id"], "title": c["title"],
                                        "artist": c["artist"], "distance": c["distance"]}
                                       for c in con.execute(
                                           "SELECT * FROM single_candidate WHERE job_id=? AND unit_key=? "
                                           "AND item_path=? ORDER BY rank",
                                           (j["job_id"], u["unit_key"], t["item_path"]))]}
                results[u["unit_key"]] = {"kind": "singles", "tracks": tracks}
        jobs[j["job_id"]] = {"settings": json.loads(j["settings_json"]), "results": results}
    con.close()
    return jobs


def canonical_results(job: dict) -> dict:
    """The subset of a job the app persists, in canonical form."""
    out = {}
    for uk in sorted(job["results"]):
        u = copy.deepcopy(job["results"][uk])
        u.pop("elapsed_s", None)
        if u["kind"] == "album":
            u.setdefault("acoustid_matched_items", None)
            for c in u["candidates"]:
                c["penalties"] = [tuple(p) for p in c["penalties"]]
        else:
            u["tracks"] = dict(sorted(u["tracks"].items()))
            for t in u["tracks"].values():
                t["acoustid_matched"] = bool(t["acoustid_matched"])
        out[uk] = u
    return {"settings": job["settings"], "results": out}


# ------------------------------------------------------------ helpers
def sha_tree(root: str) -> dict:
    out = {}
    for r, _, files in os.walk(root):
        for f in files:
            p = os.path.join(r, f)
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(p, root)] = h.hexdigest()
    return out


def make_long_tone() -> str:
    d = os.path.join(SCRATCH, "tone30s")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "01 - tone.flac")
    if not os.path.exists(p):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=30", "-metadata", "title=M0 tone",
                        "-metadata", "artist=M0 Spike", "-metadata", "album=M0 Tone", p], check=True)
    return d


def classify(unit: dict) -> str:
    if not unit["candidates"]:
        return "no_candidates"
    return "confident" if unit["recommendation"] == "strong" else "needs_review"


# ------------------------------------------------------------ main
def main():
    os.makedirs(SCRATCH, exist_ok=True)
    report = {"beets_version": H.beets.__version__, "acoustid_key_source": KEY_SOURCE,
              "acoustid_key": H.redact(ACOUSTID_KEY), "criteria": {}, "sections": {}}

    # ---- 1.1 leakage ------------------------------------------------------
    print("[1.1] server-nondeterminism control: job A live, twice", flush=True)
    live1 = strip_volatile(H.run_job("live1", SETTINGS["A"], LEAK_ALBUMS))
    live2 = strip_volatile(H.run_job("live2", SETTINGS["A"], LEAK_ALBUMS))
    mb_nondet = {
        "same_results_two_live_runs": json_roundtrip(live1["results"]) == json_roundtrip(live2["results"]),
        "top_album_ids_run1": {k: [c["album_id"] for c in u["candidates"]] for k, u in live1["results"].items()},
        "top_album_ids_run2": {k: [c["album_id"] for c in u["candidates"]] for k, u in live2["results"].items()},
    }
    cache_path = os.path.join(SCRATCH, "http_cache.json")
    if os.path.exists(cache_path):
        os.remove(cache_path)
    H.http_cache(cache_path)
    print("[1.1] negative control: naive reset, A then B in one process", flush=True)
    neg_a = H.run_job("neg_A", SETTINGS["A"], LEAK_ALBUMS, reset="naive", reset_after=False)
    neg_b = H.run_job("neg_B", SETTINGS["B"], LEAK_ALBUMS, reset="naive", reset_after=False)
    H.full_reset()
    print("[1.1] proper: full reset, A then B in one (already polluted) process", flush=True)
    job_a = H.run_job("A", SETTINGS["A"], LEAK_ALBUMS)
    job_b = H.run_job("B", SETTINGS["B"], LEAK_ALBUMS)
    print("[1.1] reference: B and A each in a fresh process", flush=True)
    fresh_b = fresh_process_job("B")
    fresh_a = fresh_process_job("A")
    in_proc_http = {"hits": H._HTTP["hits"], "misses": H._HTTP["misses"]}
    H.http_cache(None)  # everything after this talks to the live services

    sa, sb = strip_volatile(job_a), strip_volatile(job_b)
    fa, fb = strip_volatile(fresh_a), strip_volatile(fresh_b)
    nb = strip_volatile(neg_b)
    leak = {
        "method": ("Job A (plugins musicbrainz+chroma, strong_rec_thresh 0.04, countries CN, "
                   "album weight 9.0, search_limit 5) then job B (musicbrainz only, 0.5, US, "
                   "default weights, search_limit 2), sequentially in one Python process. "
                   "Each in-process job is compared with the same job run in a brand-new "
                   "process (state snapshot + full candidate output). All HTTP for this "
                   "section goes through a record/replay cache so every run sees identical "
                   "MusicBrainz/AcoustID responses (see server_nondeterminism_control)."),
        "server_nondeterminism_control": mb_nondet,
        "http_cache": {"in_process": in_proc_http,
                       "fresh_B": fresh_b.get("http"), "fresh_A": fresh_a.get("http")},
        "negative_control_naive_reset": {
            "state_diff_vs_fresh_B": dict_diff(json_roundtrip(nb["state_at_start"]), fb["state_at_start"]),
            "results_equal_fresh_B": json_roundtrip(nb["results"]) == fb["results"],
        },
        "full_reset": {
            "B_state_diff_vs_fresh": dict_diff(json_roundtrip(sb["state_at_start"]), fb["state_at_start"]),
            "B_results_equal_fresh": json_roundtrip(sb["results"]) == fb["results"],
            "A_state_diff_vs_fresh": dict_diff(json_roundtrip(sa["state_at_start"]), fa["state_at_start"]),
            "A_results_equal_fresh": json_roundtrip(sa["results"]) == fa["results"],
            "state_after_reset": H.state_snapshot(),
        },
    }
    if not leak["full_reset"]["B_results_equal_fresh"]:
        leak["full_reset"]["B_results_detail"] = {"in_process": sb["results"], "fresh": fb["results"]}
    report["criteria"]["no_leakage"] = (not leak["full_reset"]["B_state_diff_vs_fresh"]
                                        and leak["full_reset"]["B_results_equal_fresh"]
                                        and not leak["full_reset"]["A_state_diff_vs_fresh"]
                                        and leak["full_reset"]["A_results_equal_fresh"])
    report["sections"]["1.1_no_leakage"] = leak

    # ---- 1.4 acoustid on a fingerprintable tone ----------------------------
    print("[1.4] AcoustID lookup on a 30 s generated tone", flush=True)
    H.ACOUSTID_TRACE.clear()
    tone_job = H.run_job("tone", SETTINGS["A"], {"tone30s": make_long_tone()})
    tone_trace = list(H.ACOUSTID_TRACE)

    # ---- 1.6 real samples --------------------------------------------------
    print("[1.6] copying nas_sample -> real_scratch (APFS clone)", flush=True)
    nas_before = sha_tree(NAS_SAMPLE)
    if os.path.exists(REAL):
        shutil.rmtree(REAL)
    subprocess.run(["cp", "-c", "-R", NAS_SAMPLE, REAL], check=True)
    real_before = sha_tree(REAL)
    real_albums = {
        "album_chinese_flac": os.path.join(REAL, "album_chinese_flac"),
        "album_ape_format": os.path.join(REAL, "album_ape_format"),
        "album_clean_mp3_compilation (2 discs)": os.path.join(REAL, "album_clean_mp3_compilation"),
        # batch 2 (scripts/copy_samples_2.sh)
        "album_garbled_gbk_traditional_xrcd": os.path.join(REAL, "album_garbled_gbk_traditional_xrcd"),
        "album_traditional_hk": os.path.join(REAL, "album_traditional_hk"),
        "album_remaster_xrcd2": os.path.join(REAL, "album_remaster_xrcd2"),
        "album_various_artists_hk": os.path.join(REAL, "album_various_artists_hk"),
    }
    real_singles = {"singles_chinese_loose": os.path.join(REAL, "singles_chinese_loose"),
                    "singles_live": os.path.join(REAL, "singles_live"),
                    "singles_with_lrc": os.path.join(REAL, "singles_with_lrc"),
                    "singles_dupes_blank_album": os.path.join(REAL, "singles_dupes_blank_album")}
    real_albums = {k: v for k, v in real_albums.items() if os.path.isdir(v)}
    real_singles = {k: v for k, v in real_singles.items() if os.path.isdir(v)}
    H.ACOUSTID_TRACE.clear()
    print("[1.6] matching real samples (MusicBrainz is rate-limited; takes a few minutes)", flush=True)
    real_job = H.run_job("real", SETTINGS["A"], real_albums, real_singles)
    real_trace = list(H.ACOUSTID_TRACE)
    real_after = sha_tree(REAL)
    nas_after = sha_tree(NAS_SAMPLE)

    # ---- 1.4 report --------------------------------------------------------
    parsed_ok = [t for t in tone_trace + real_trace if t.get("status") == "ok"]
    report["sections"]["1.4_acoustid"] = {
        "key_source": KEY_SOURCE,
        "how_key_is_passed": ("beetsplug.chroma.API_KEY (a module constant in beets 2.14.1, not a config "
                              "option) is set from settings['acoustid_key'] by the harness and reset to the "
                              "shipped default after every job. config acoustid.apikey is only the *user* "
                              "submission key and is not used for lookups."),
        "tone_trace": tone_trace,
        "real_requests": len(real_trace),
        "real_requests_status_ok": sum(1 for t in real_trace if t.get("status") == "ok"),
        "real_requests_with_results": sum(1 for t in real_trace if t.get("n_results")),
        "real_requests_with_linked_mb_recordings": sum(1 for t in real_trace if t.get("top_recordings")),
        "finding": ("AcoustID recognises nearly every real file (score ~0.97-0.99) but most "
                    "fingerprints have no linked MusicBrainz recording, so chroma yields no "
                    "candidates for them (compare real_requests_with_results vs "
                    "real_requests_with_linked_mb_recordings)."),
        "real_trace_sample": real_trace[:3],
        "tone_job_acoustid_matched_items": tone_job["results"]["tone30s"].get("acoustid_matched_items"),
    }
    report["criteria"]["acoustid_real_lookup_parsed"] = bool(parsed_ok)
    report["criteria"]["acoustid_used_user_key"] = bool(ENV_KEY) and bool(parsed_ok)

    # ---- 1.2 no library file / side effects --------------------------------
    all_jobs = {"neg_A": neg_a, "neg_B": neg_b, "A": job_a, "B": job_b, "tone": tone_job, "real": real_job}
    events = {}
    for name, j in all_jobs.items():
        # exclude /dev/null and the harness's own HTTP replay cache (test instrumentation)
        ev = [e for e in j["fs_events"] if not (e[0] == "open-write" and (
            e[1] == "/dev/null" or e[1].startswith(SCRATCH + os.sep)))]
        kinds = {}
        for kind, path in ev:
            kinds.setdefault(kind, set()).add(path if kind != "subprocess" else path.split(" ")[0])
        events[name] = {"python_level_events": {k: sorted(v) for k, v in kinds.items()},
                        "dir_diffs": j["fs_diff"]}
    non_subprocess = {n: {k: v for k, v in e["python_level_events"].items() if k != "subprocess"}
                      for n, e in events.items()}
    any_writes = any(v for v in non_subprocess.values()) or any(e["dir_diffs"] for e in events.values())
    report["sections"]["1.2_no_library_file"] = {
        "library": "beets.library.Library(':memory:', set_music_dir=False)",
        "required_setting": "create_backup_before_migrations: no",
        "finding": ("With the default config, constructing Library(':memory:') writes 11 sqlite backup "
                    "files named ':memory:-before-<table>-<migration>.bak' (53 KB each) into the CWD and "
                    "prints 'Created database backup at ...' to stdout. Found on the first smoke run, "
                    "files deleted; the harness now sets create_backup_before_migrations=False. The "
                    "Python audit hook did NOT see those writes (sqlite backup API writes from C), which "
                    "is why every job also runs in an empty CWD with before/after directory diffs."),
        "watched_dirs": "job CWD (empty temp dir), BEETSDIR (empty temp dir), ~/.config/beets, every source dir",
        "per_job": events,
        "subprocesses_note": "ffmpeg is spawned read-only by pyacoustid/audioread to decode audio for fingerprints",
        "source_hashes_unchanged": {"nas_sample": nas_before == nas_after, "real_scratch": real_before == real_after},
        "home_beets_config_exists": os.path.exists(os.path.expanduser("~/.config/beets")),
    }
    report["criteria"]["no_side_effects"] = (not any_writes and nas_before == nas_after
                                             and real_before == real_after
                                             and not os.path.exists(os.path.expanduser("~/.config/beets")))
    report["criteria"]["no_prompts"] = True
    report["sections"]["no_prompts"] = ("Structural: only tag_album/tag_item are called; no ImportSession, "
                                        "no UI module imported. stdin is never read (script completes "
                                        "unattended under -I).")
    report["criteria"]["explicit_settings"] = (not os.path.exists(os.path.expanduser("~/.config/beets"))
                                               and not leak["full_reset"]["B_state_diff_vs_fresh"])

    # ---- 1.3 save / reload --------------------------------------------------
    print("[1.3] save -> sqlite -> reload", flush=True)
    persisted = {"A": job_a, "B": job_b, "real": real_job}
    save_jobs(APP_DB, persisted)
    reloaded = load_jobs(APP_DB)
    eq = {k: canonical_results(json_roundtrip(persisted[k])) == canonical_results(reloaded[k])
          for k in persisted}
    # json_roundtrip turns tuples into lists; canonical_results re-tuples penalties for both sides
    n_map = sqlite3.connect(APP_DB).execute("SELECT count(*) FROM mapping").fetchone()[0]
    report["sections"]["1.3_save_reload"] = {
        "db": os.path.relpath(APP_DB, ROOT),
        "tables": ["job", "unit", "candidate", "mapping", "single_track", "single_candidate"],
        "mapping_rows": n_map,
        "equal_after_reload": eq,
        "key": "mapping rows are keyed by (candidate, item path relative to the album dir), not object identity",
    }
    report["criteria"]["survives_save_reload"] = all(eq.values()) and n_map > 0

    # ---- 1.5 APE -------------------------------------------------------------
    have_mac = shutil.which("mac") is not None
    report["sections"]["1.5_ape"] = {
        "mac_cli_available": have_mac,
        "homebrew_formula": "none (brew info monkeys-audio -> no formula)",
        "ffmpeg_ape_encoder": "none (ffmpeg has an APE decoder only)",
        "fallback": "real APE+CUE album from nas_sample used in 1.6 (album_ape_format)",
        "beets_reads_ape": real_job["results"]["album_ape_format"]["n_items"] == 1,
    }

    # ---- 1.6 match quality ---------------------------------------------------
    mq = {"albums": {}, "singles": {}, "summary": {"confident": 0, "needs_review": 0, "no_candidates": 0}}
    for k, u in real_job["results"].items():
        if u["kind"] == "album":
            cls = classify(u)
            top = u["candidates"][0] if u["candidates"] else None
            mq["albums"][k] = {
                "class": cls, "recommendation": u["recommendation"], "n_items": u["n_items"],
                "acoustid_matched_items": u.get("acoustid_matched_items"),
                "n_candidates": len(u["candidates"]),
                "top": None if not top else {
                    "album": top["album"], "artist": top["artist"], "year": top["year"],
                    "country": top["country"], "distance": round(top["distance"], 4),
                    "album_id": top["album_id"], "penalties": top["penalties"],
                    "items_mapped": len(top["mapping"]), "extra_items": len(top["extra_items"]),
                    "extra_tracks": len(top["extra_tracks"]),
                    "rendered_destinations_sample": dict(list((top["rendered_destinations"] or {}).items())[:3]),
                },
                "elapsed_s": u.get("elapsed_s"),
            }
            mq["summary"][cls] += 1
        else:
            for path, t in u["tracks"].items():
                cls = classify(t)
                top = t["candidates"][0] if t["candidates"] else None
                mq["singles"][path] = {"class": cls, "recommendation": t["recommendation"],
                                       "acoustid_matched": t["acoustid_matched"],
                                       "top": None if not top else {k2: top[k2] for k2 in ("title", "artist", "distance", "recording_id")}}
                mq["summary"][cls] += 1
    mq["classification_rule"] = ("confident = beets recommendation 'strong' (top distance < "
                                 f"{SETTINGS['A']['strong_rec_thresh']}); needs_review = any candidates "
                                 "but not strong; no_candidates = nothing returned")
    report["sections"]["1.6_match_quality"] = mq

    with open(os.path.join(RESULTS, "m0a_result.json"), "w") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(RESULTS, "m0a_raw_jobs.json"), "w") as fh:
        json.dump({k: v for k, v in all_jobs.items()}, fh, indent=1, ensure_ascii=False, default=str)
    print(json.dumps(report["criteria"], indent=2))
    print(json.dumps(mq["summary"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh-job")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.fresh_job:
        H.http_cache(os.path.join(SCRATCH, "http_cache.json"))
        job = strip_volatile(H.run_job(a.fresh_job, SETTINGS[a.fresh_job], LEAK_ALBUMS))
        job["http"] = {"hits": H._HTTP["hits"], "misses": H._HTTP["misses"]}
        with open(a.out, "w") as fh:
            json.dump(job, fh, ensure_ascii=False, default=str)
    else:
        main()
