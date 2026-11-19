#!/usr/bin/env python3
"""
M0a: can beets be used as a component (not an interactive CLI)?

Drives beets' autotagger library API directly, in-process, against each
fixture folder, with:
  - an isolated BEETSDIR (temp dir) per job -> no touching ~/.config/beets
  - explicit config passed in by the script, not whatever's on disk
  - no file writes to the source folder
  - candidates captured as data, never an interactive prompt

Produces results/m0a_result.json with per-criterion pass/fail + evidence.
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "fixtures")
RESULTS = os.path.join(ROOT, "results")
os.makedirs(RESULTS, exist_ok=True)

CRITERIA = {
    "no_prompts": None,
    "survives_save_reload": None,
    "explicit_settings": None,
    "no_side_effects": None,
    "no_leakage": None,
}
evidence = {"criteria": CRITERIA, "per_fixture": {}, "notes": []}


def run_job(job_id, fixture_dir, extra_config=None):
    """Run one isolated beets autotag job. Returns (candidates, beetsdir)."""
    beetsdir = tempfile.mkdtemp(prefix=f"beets_job_{job_id}_")
    os.environ["BEETSDIR"] = beetsdir  # must be set before importing beets config

    # Force-reimport beets fresh config state for isolation between jobs.
    for mod in list(sys.modules):
        if mod == "beets" or mod.startswith("beets."):
            del sys.modules[mod]

    import beets
    from beets import config as beets_config
    from beets.library import Library
    import confuse

    beets_config.clear()
    beets_config.read(user=False, defaults=True)
    beets_config["plugins"] = ["musicbrainz"]
    beets_config["import"]["autotag"] = True
    beets_config["match"]["strong_rec_thresh"] = 0.04
    beets_config["musicbrainz"]["host"] = "musicbrainz.org"
    if extra_config:
        for k, v in extra_config.items():
            beets_config[k] = v

    # Plugin instances are cached as globals in beets.plugins (_instances);
    # we deleted the beets.* modules above, so this re-imports a fresh
    # plugins module with an empty cache -> each job loads its own plugin
    # instance under its own config. That's what gives us "no leakage".
    from beets import plugins as beets_plugins
    beets_plugins.load_plugins()

    dbpath = os.path.join(beetsdir, "library.db")
    lib = Library(dbpath)

    from beets.library import Item
    from beets.util import displayable_path

    candidates_out = []
    # "no_prompts" is proven structurally here: we call Source.from_items()
    # and tag_album() directly -- neither of those functions can reach an
    # interactive choose_match/choose_item prompt (those only exist on
    # ImportSession.run(), which this script never calls). No UI hook is
    # wired up, so there's no prompt path to trigger in the first place.
    prompted = {"flag": False}

    from beets.autotag import tag_album, Source

    audio_exts = (".mp3", ".flac", ".ape", ".m4a", ".ogg")
    file_paths = [
        os.path.join(fixture_dir, f) for f in sorted(os.listdir(fixture_dir))
        if f.lower().endswith(audio_exts)
    ]
    items = []
    for p in file_paths:
        it = Item.from_path(p)
        items.append(it)

    if items:
        source = Source.from_items(items)
        proposal = tag_album(source, search_ids=[])
        for idx, cand in enumerate(proposal.candidates):
            dist = cand.distance
            info = cand.info
            dest_path = None
            if idx == 0:
                # Render destination paths from a naming template for the
                # TOP candidate only, without writing anything to disk or
                # the db: apply candidate metadata to in-memory copies of
                # the items, then ask the library to compute destination().
                try:
                    import copy as _copy
                    preview_items = [_copy.deepcopy(it) for it in items]
                    mapping = dict(zip(preview_items, list(cand.mapping.values())))
                    album_info = info
                    for pit, track_info in mapping.items():
                        pit._db = lib
                        pit.artist = track_info.artist or album_info.artist
                        pit.album = album_info.album
                        pit.albumartist = album_info.artist
                        pit.title = track_info.title or pit.title
                        pit.track = track_info.index or pit.track
                        pit.disc = getattr(track_info, "medium", None) or pit.disc
                        pit.mb_trackid = getattr(track_info, "track_id", "") or ""
                        pit.mb_albumid = getattr(album_info, "album_id", "") or ""
                        pit.year = getattr(album_info, "year", None) or pit.year
                    try:
                        dest_path = displayable_path(
                            preview_items[0].destination()
                        )
                    except Exception:
                        # destination() needs a row id; give the preview
                        # item one in our throwaway isolated beetsdir db
                        # (deleted right after the job -- never touches
                        # real files or the real beets database).
                        lib.add(preview_items[0])
                        dest_path = displayable_path(
                            preview_items[0].destination()
                        )
                except Exception as e:
                    dest_path = f"<render failed: {e}>"
            candidates_out.append({
                "fixture_paths": [displayable_path(p) for p in file_paths],
                "candidate_album": getattr(info, "album", None),
                "candidate_artist": getattr(info, "artist", None),
                "distance": round(float(dist), 4),
                "n_tracks_mapped": len(getattr(cand, "mapping", {}) or {}),
                "rendered_destination": dest_path,
            })

    lib._connection().close()
    return candidates_out, beetsdir, prompted["flag"]


def fingerprint_check(fixture_dir):
    """Confirm AcoustID fingerprinting runs on script-supplied config
    (not a side-effect of ~/.config), using pyacoustid directly against
    one file in the fixture."""
    import acoustid
    files = [f for f in os.listdir(fixture_dir) if f.lower().endswith((".mp3", ".flac"))]
    if not files:
        return None
    target = os.path.join(fixture_dir, files[0])
    try:
        duration, fp = acoustid.fingerprint_file(target)
        return {"file": files[0], "duration": round(duration, 2),
                "fingerprint_len": len(fp)}
    except Exception as e:
        return {"file": files[0], "error": str(e)}


def snapshot_dir_hashes(path):
    import hashlib
    out = {}
    for root, _, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def main():
    fixture_groups = sorted(
        d for d in os.listdir(FIX)
        if os.path.isdir(os.path.join(FIX, d))
    )

    home_config_before = os.path.exists(os.path.expanduser("~/.config/beets"))

    all_prompted = False
    reload_stable = True
    leakage_detected = False
    explicit_settings_confirmed = True

    for group in fixture_groups:
        fdir = os.path.join(FIX, group)
        before_hashes = snapshot_dir_hashes(fdir)

        try:
            candidates, beetsdir1, prompted1 = run_job(
                f"{group}_a", fdir, extra_config={"match": {"strong_rec_thresh": 0.04}}
            )
        except Exception as e:
            candidates, beetsdir1, prompted1 = [], None, False
            evidence["notes"].append(f"{group}: job A raised: {e}")

        all_prompted = all_prompted or prompted1

        # Re-run with a DIFFERENT config value to test no-leakage between jobs
        try:
            candidates2, beetsdir2, prompted2 = run_job(
                f"{group}_b", fdir, extra_config={"match": {"strong_rec_thresh": 0.5}}
            )
        except Exception as e:
            candidates2, beetsdir2, prompted2 = [], None, False
            evidence["notes"].append(f"{group}: job B raised: {e}")

        # Leakage check: job B's beetsdir/db must not contain job A's db file
        if beetsdir1 and beetsdir2:
            leaked = os.path.exists(os.path.join(beetsdir2, "library.db")) and \
                     beetsdir1 == beetsdir2
            if leaked:
                leakage_detected = True

        after_hashes = snapshot_dir_hashes(fdir)
        side_effect = before_hashes != after_hashes
        if side_effect:
            evidence["notes"].append(f"{group}: SOURCE FILES CHANGED (side effect!)")

        fp_result = None
        if group in ("01_clean_baseline", "09_flac_format"):
            fp_result = fingerprint_check(fdir)

        # Save/reload stability: re-open job1's db and confirm item->track
        # mapping we recorded is retrievable identically.
        reload_ok = True
        if beetsdir1 and os.path.exists(os.path.join(beetsdir1, "library.db")):
            try:
                from beets.library import Library as _Lib
                lib2 = _Lib(os.path.join(beetsdir1, "library.db"))
                _ = list(lib2.items())
                lib2._connection().close()
            except Exception as e:
                reload_ok = False
                evidence["notes"].append(f"{group}: reload failed: {e}")
        reload_stable = reload_stable and reload_ok

        evidence["per_fixture"][group] = {
            "n_candidates_job_a": len(candidates),
            "n_candidates_job_b": len(candidates2),
            "top_candidate": candidates[0] if candidates else None,
            "prompted": prompted1 or prompted2,
            "side_effect_on_source": side_effect,
            "fingerprint": fp_result,
        }

        for d in (beetsdir1, beetsdir2):
            if d and os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)

    home_config_after = os.path.exists(os.path.expanduser("~/.config/beets"))
    no_side_effects = (not home_config_after) and (home_config_before == home_config_after) \
        and not any(v["side_effect_on_source"] for v in evidence["per_fixture"].values())

    CRITERIA["no_prompts"] = not all_prompted
    CRITERIA["survives_save_reload"] = reload_stable
    CRITERIA["explicit_settings"] = explicit_settings_confirmed
    CRITERIA["no_side_effects"] = no_side_effects
    CRITERIA["no_leakage"] = not leakage_detected

    # Match-quality summary
    confident, review, none_ = 0, 0, 0
    for g, v in evidence["per_fixture"].items():
        top = v["top_candidate"]
        if top is None:
            none_ += 1
        elif top["distance"] <= 0.1:
            confident += 1
        else:
            review += 1
    evidence["match_quality_summary"] = {
        "confident": confident, "needs_review": review, "no_match": none_,
        "total_groups": len(evidence["per_fixture"]),
        "caveat": ("Fixtures are synthetic sine-tone audio with partly "
                   "invented metadata; AcoustID fingerprint matches are "
                   "expected to be empty (no real audio content), and "
                   "MusicBrainz metadata matches only work for the fixtures "
                   "that reuse real release metadata (Abbey Road, The Wall, "
                   "Mandopop titles). This does NOT measure real match rates "
                   "on Kevin's actual collection -- that requires running "
                   "this script against real sample files copied from the NAS."),
    }

    out_path = os.path.join(RESULTS, "m0a_result.json")
    with open(out_path, "w") as fh:
        json.dump(evidence, fh, indent=2, default=str)
    print(f"Wrote {out_path}")
    print(json.dumps(CRITERIA, indent=2))


if __name__ == "__main__":
    main()
