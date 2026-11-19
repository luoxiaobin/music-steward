# MP3LibraryTool — M0 Handoff

**Date:** 2026-11-18
**Handing off to:** Claude Code (continuing from Hermes/KLAI2 session)
**Project root:** `~/Projects/MP3LibraryTool/m0/`
**Spec source:** `~/Downloads/MP3LibraryTool — M0 Experiments.md` (read this first — it's the original spec these two spikes were built to satisfy)

## 1. What this project is

Before building any UI, the user wants two risky assumptions de-risked with
throwaway spike scripts, per the M0 spec doc:

- **M0a** — can `beets` be used as an embedded *library* (not interactive
  CLI) to get match candidates and distances programmatically, with no
  prompts, no global side effects, and no state leakage between jobs?
- **M0b** — can files be copied/tagged/renamed on a destination
  (NAS/local) safely: crash-mid-operation recovery, conflict detection,
  tag preservation per audio format, and side-file (`.cue`/`.lrc`)
  handling?

Both spikes are **done and passing**. This handoff tells you what was
built, what was found, what's still open, and exactly how to resume.

## 2. Environment state

- Python venv at `m0/venv/` (already has `beets`, `mutagen`, `pyacoustid`,
  `musicbrainzngs` installed — don't reinstall, just `source venv/bin/activate`
  or call `./venv/bin/python` directly).
- Homebrew: `chromaprint` and `beets` installed via `brew install chromaprint beets`
  (system-wide, outside the venv — only used for CLI probing during
  investigation, the venv's own beets copy is what the scripts import).
- `ffmpeg` on this Mac has Monkey's Audio (APE) codec support confirmed
  (used by `audio_hash()` in M0b to decode PCM for format-agnostic
  audio-content hashing).
- **beets version note:** this install's embedded API differs from a lot
  of beets docs/tutorials you'll find online. No `albums_in_dir()`. Use:
  ```python
  from beets import config as beets_config, plugins as beets_plugins
  from beets.autotag import Source, tag_album
  beets_config.clear(); beets_config.read(user=False, defaults=True)
  beets_config["plugins"] = ["musicbrainz"]
  beets_plugins.load_plugins()   # REQUIRED — just setting config["plugins"] does nothing
  source = Source.from_items(items)
  proposal = tag_album(source, search_ids=[])
  # proposal.candidates -> list of AlbumMatch(distance, info, mapping)
  ```
  Destination path rendering needs the item to have a row id in a real
  (even throwaway) beets db: `lib.add(item)` then `item.destination()`
  works; `lib.destination(item)` does **not** exist on this version.

## 3. NAS access state (important, unresolved)

- NAS is a Synology (`<nas-name>`, `<NAS-IP>`, hostname
  `<nas-hostname>`), reachable on the home LAN.
- `mount_smbfs //<user>@<NAS-IP>/music ~/NAS` with the real DSM
  account **failed with "Authentication error"** — cause not yet
  diagnosed (candidates: wrong password, DSM Auto Block lockout, wrong
  username, or 2FA requiring an app password instead of the normal login).
- Currently mounted via **guest account**, read-only, at
  `/Users/<user>/NAS` (confirmed read-only: `touch` inside it fails
  with Permission denied, `ln` fails).
- **Consequence:** M0b was run against a **local disk** stand-in
  (`dest_local/`), not the real NAS. It proves the crash/conflict/tag
  logic, but NOT NAS-specific transport behavior (does SMB support hard
  links? what does a real dropped SMB session look like mid-copy vs. our
  simulated `kill -9`?). **This is the single biggest open item** — see
  Section 6.

## 4. Files in `~/Projects/MP3LibraryTool/m0/`

```
scripts/
  make_fixtures.py            # generates synthetic sine-tone MP3/FLAC fixtures (M0a)
  m0a_beets_spike.py           # M0a: beets-as-library spike (see Section 5)
  m0b_nas_safety_spike.py      # M0b: copy/tag/crash/conflict safety spike (see Section 5)
fixtures/                      # 10 synthetic fixture albums for M0a (clean, mojibake,
                                # simplified/traditional Chinese, multi-disc, compilation,
                                # live, untagged, FLAC, side-files) + MANIFEST.txt
nas_sample/                     # ~982MB REAL files copied read-only from the NAS, used
                                # as M0b's source material. Four samples:
  album_chinese_flac/           #   郑绪岚《情系红楼梦》— 11 FLAC tracks, Chinese tags
  album_ape_format/             #   HIFI年代1 怀念战友 — APE + .cue + .txt side-files
  album_clean_mp3_compilation/  #   Phil Collins Love Songs — clean Western MP3 baseline
  singles_chinese_loose/        #   15 loose 童丽 singles, mixed FLAC/MP3
dest_local/                     # M0b's scratch destination (local disk stand-in for NAS).
                                # Safe to delete/regenerate — script calls reset_dirs() on
                                # every run.
stage_incoming/                 # M0b scratch staging dir, also safe to delete/regenerate.
results/
  m0a_result.json              # M0a evidence: per-fixture candidates + 5 pass criteria
  m0b_result.json              # M0b evidence: crash/conflict/tag results + 2 findings
venv/                           # Python venv, already populated, don't reinstall
```

**IMPORTANT — never modify:** `nas_sample/` is the read-only reference
copy from the real NAS. Scripts only ever *read* from it. If you need a
bigger/different sample, copy more from `/Users/<user>/NAS/` (guest,
read-only) rather than editing what's there.

## 5. What the two scripts actually do and proved

### `m0a_beets_spike.py`
For each of the 10 synthetic fixtures, runs two independent beets jobs
(different match thresholds) each in its own isolated `BEETSDIR` (temp
dir, deleted after), gets AcoustID/MusicBrainz candidates via
`tag_album()`, and for the top candidate renders a destination path from
a naming template (via `lib.add()` + `item.destination()`) without
writing real files.

**All 5 pass criteria green:** no prompts, survives save/reload, explicit
settings (never touches `~/.config/beets`), no side effects on source
files, no state leakage between the two jobs per fixture.

**Caveat already known:** fixtures are synthetic sine-tone audio, so
AcoustID fingerprinting finds nothing and MusicBrainz metadata search
only matched 1/10 fixtures (Abbey Road, by title/artist text, not audio).
This measured **mechanism**, not real-world match rates — that requires
running against real audio, which `nas_sample/` now provides and which
M0b partially exercises (tag round-trip) but M0a's matching logic has NOT
yet been re-run against real audio. **Open item**, see Section 6.

### `m0b_nas_safety_spike.py`
Runs against `dest_local/` (local disk) using real files from
`nas_sample/` as source. Tests, all passing after two bugs were found and
fixed during this session (see below):

1. **Safe-publish probe**: hard links supported on this filesystem (APFS);
   `os.link()` correctly refuses to overwrite an existing name
   (`FileExistsError`); `O_EXCL` open also refuses. These are the two
   atomic "claim this name or fail" primitives the real tool should use.
2. **Crash injection**: a journaled pipeline (`planned → staged → tagged →
   published → committed`, logged to JSON at each step) is crashed via
   `os.fork()` + `os._exit(137)` (simulates `kill -9`) right after each
   step, then rerun. Verified at every crash point: exactly one final
   file, no orphan `.part` temp files, no duplicates.
3. **Conflict scenarios**: destination already has same-named file at
   publish time (detected, pre-existing file left untouched); source file
   mutates mid-copy (detected via stat snapshot diff); identical file
   already at destination (classified via audio-content hash, ignoring
   tags); output edited after publish but before undo (detected via
   audio-content hash — this is the "no silent discard of a user edit"
   guarantee).
4. **Tag round-trip**: change one field (title), save, reread, diff. MP3
   and FLAC: all other fields preserved, audio bytes unchanged (verified
   via `ffmpeg`-decoded-PCM hash, format-agnostic). **APE: no embedded
   tags found at all** — see finding below.
5. **Side files**: `.cue` sheet reference-rewrite on rename attempted —
   see finding below. No `.lrc` files existed in the sample set so that
   check didn't run (untested, not failed).

**Bug found and fixed during this session:** crashing right after
"published" (dest already has the real file via hard link) correctly
detects the conflict on rerun, but the rerun's own staged `.part` file was
being left behind uncleaned. Fixed: cleanup now runs on the conflict path
regardless of outcome. This is exactly the class of bug this spike exists
to catch before real code ships.

## 6. Real findings from your actual NAS collection (not synthetic — these matter for design)

1. **APE files in `音乐Ape/` carry zero embedded tags.** Metadata for
   that album lives entirely in the sibling `.cue` sheet. Any tagging
   step for APE must parse the cue sheet, not rely on `mutagen`/APEv2
   tags on the audio file itself.
2. **Those `.cue` sheets are GBK/GB2312-encoded, not UTF-8.** Reading them
   naively as UTF-8 produces mojibake (confirmed:
   `FILE "٤�����&..."` instead of the real Chinese filename). Any
   cue-sheet parser needs encoding auto-detection (e.g. `chardet`/
   `cchardet`/`charset-normalizer`) before parsing text, and this broke
   the cue-rewrite-on-rename test in M0b (the regex matched against
   garbled text, so the rewritten reference didn't match the real
   on-disk filename — this is evidence of the problem, not a script bug).
3. The NAS has **huge folders** (e.g. `Ape.Flac/` alone is 501GB) with a
   mix of real multi-track album directories named like single files
   (e.g. a directory literally named `红楼梦-郑绪岚-情系红楼梦.flac`
   containing 11 real `.flac` tracks inside) — the real-world "is this a
   file or a folder" ambiguity the spec worried about is confirmed to
   exist in the actual collection, not just hypothetically.

## 7. Open items / next steps, in priority order

1. **Fix NAS authentication** so we get real (even if still read-only
   for now, ideally read-write for a test share) access instead of guest.
   Diagnose: check DSM Control Panel → Security → Auto Block (possible
   IP lockout from the failed attempts during this session), confirm the
   actual DSM account username, check whether 2FA is enabled (needs an
   app password for SMB, not the normal login password).
2. **Re-run M0b's crash/conflict/hardlink tests against the real NAS**
   over SMB once write access exists — local disk (APFS) behavior for
   hard links and atomic rename is not guaranteed to match SMB/Synology
   filesystem behavior. This is the single most important remaining gap.
3. **Re-run M0a's beets matching logic against real audio** (use
   `nas_sample/` instead of synthetic fixtures) to get a realistic read
   on AcoustID/MusicBrainz match rates, since the synthetic sine-tone
   fixtures couldn't exercise fingerprinting at all.
4. **Design a cue-sheet parser with encoding detection** for the
   APE-with-no-tags case — needed before APE albums can be tagged at all.
5. **Decide on the "folder-named-like-a-file" convention** —
   `XXX.flac`/`XXX.ape` as a *directory* containing real tracks shows up
   in the real collection; whatever path-walking logic the real tool
   uses must treat directory vs. file by `os.path.isdir()`, never by
   extension alone.
6. Once NAS access is sorted and M0a/M0b re-validated against it, the
   two spikes together should give enough confidence to start designing
   the actual MP3LibraryTool UI/architecture (currently not started,
   this was explicitly "experiments before building any UI" per the
   spec).

## 8. How to resume

```bash
cd ~/Projects/MP3LibraryTool/m0
source venv/bin/activate   # or use ./venv/bin/python directly
./venv/bin/python scripts/m0a_beets_spike.py   # re-run M0a, ~1-2 min
./venv/bin/python scripts/m0b_nas_safety_spike.py   # re-run M0b, ~1-2 min
cat results/m0a_result.json
cat results/m0b_result.json
```

Both scripts are idempotent (reset their own scratch dirs at the start of
each run) and safe to rerun repeatedly without manual cleanup.
