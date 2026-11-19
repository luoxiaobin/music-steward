# MP3LibraryTool — M0 Evidence Report

**Date:** 2026-11-18 · **beets:** 2.14.1 (pinned, `m0/requirements.lock`) · **Python:** 3.12.16
**Status:** M0a complete. M0b complete on **local disk only** — the NAS run is blocked (see §5).

## 1. Summary

| Question | Answer | Confidence |
|---|---|---|
| Can beets be used as a library? | **Yes**, behind a harness that resets beets' process-wide state (or runs each job in its own worker process) and disables migration backups. | High |
| Does matching work on your music? | **Mechanically yes, practically weak for Chinese releases.** 0 of 18 sample units matched confidently; the only correct top candidate was the Western 2-CD set. | Medium (small sample) |
| Can files be copied/tagged safely? | **Yes on local APFS**: journal-first protocol survives a crash at every step with no duplicates, no overwrite, no half-written final, every item accounted for. | High (local) |
| …on the NAS? | **Not yet tested.** Mount is guest/read-only and no scratch path was given. | — |
| Publish method | Local APFS: `renamex_np(RENAME_EXCL)` (preferred) and `link()+unlink()` both refuse to overwrite. NAS: unknown. | High (local) |
| Tags survive an edit (MP3/FLAC/APE)? | **Yes**, every field (custom, multi-artist, artwork, lyrics, ReplayGain) with both mutagen and beets' `mediafile`; `mediafile` renames APE key `Title`→`TITLE` (harmless per spec). | High |

## 2. M0a — beets as a component (`scripts/m0a_v2.py`, `scripts/beets_harness.py`)

Evidence: `results/m0a_result.json` (summary), `results/m0a_raw_jobs.json` (every candidate), `results/m0a_app.sqlite`.

| # | Criterion | Result |
|---|---|---|
| 1.1 | No leakage between two jobs in the same process | **Pass** (with full reset) · **Fail** with config-only reset |
| 1.2 | No library file on disk / no side effects | **Pass** after one required setting (finding F2) |
| 1.3 | Survives save → SQLite → reload | **Pass** — exact equality, 256 mapping rows |
| 1.4 | Real AcoustID lookup made and parsed | **Pass (mechanism)** — but with beets' built-in app key, **not yours** (`ACOUSTID_API_KEY` not set) |
| 1.5 | APE fixtures | **No encoder available** → used the real APE+CUE album from the NAS sample |
| 1.6 | Match quality on real files | 0 confident · 18 needs review · 0 no candidates (detail below) |
| — | No prompts | **Pass** — only `tag_album`/`tag_item` are called; runs unattended |
| — | Explicit settings | **Pass** — `BEETSDIR` is an empty temp dir, `user=False`, `~/.config/beets` never created |

### 1.1 Leakage — what was done and why it matters
Job A (MusicBrainz + chroma, `strong_rec_thresh` 0.04, preferred country CN, album weight 9.0, search limit 5) then job B (MusicBrainz only, 0.5, US, default weights, search limit 2), one after the other in one Python process. Each in-process job was compared with the same job run in a **brand-new process**: beets' global-state snapshot and the complete candidate output.

* **Negative control (config re-read only, what the first spike relied on):** job B ran with job A's plugins, event listeners, AcoustID cache, API key and **album distance weight 9.0** instead of 3.0, and MusicBrainz raised `search_query_ascii not found`. Results differed from a fresh process.
* **Full reset:** state and results identical to the fresh process, for both A and B — in a process that had just been polluted by the negative control.
* **Control for server nondeterminism:** two live runs of the *same* job A returned *different* sets of equally-scored Abbey Road releases. So the leak test replays recorded HTTP responses (fresh processes made 0 live requests). Without this, the first attempt wrongly looked like a leak.

beets 2.14.1 process-global state the harness must reset (all verified): `beets.config`; `plugins._instances` (`load_plugins()` is a no-op once filled); `BeetsPlugin.listeners/_raw_listeners`; `BeetsPluginMeta.template_*`; `functools.cache` on `find_metadata_source_plugins`, `get_metadata_source`, `get_penalty`; `cached_classproperty.cache` (e.g. distance weights); `beetsplug.chroma._matches/_fingerprints/_acoustids`; `beetsplug.chroma.API_KEY`. Several of these are private, so **any beets upgrade must re-run this test.**

### 1.2 Side effects
Every job runs in an empty temp CWD with an empty `BEETSDIR`; a Python audit hook logs every write-open, sqlite connect and subprocess; before/after snapshots cover CWD, `BEETSDIR`, `~/.config/beets` and every source dir; source trees are SHA-256'd before and after.
Result: no writes; sources byte-identical; only subprocess is `ffmpeg` (read-only decode for fingerprints).

### 1.3 Save / reload
Schema `job / unit / candidate / mapping / single_track / single_candidate`; mapping rows are keyed by *(candidate, file path relative to the album dir)*, never object identity. Jobs A, B and the real-sample job reload **exactly equal** (floats included).

### 1.4 AcoustID
* beets 2.14.1 uses a **module constant** `beetsplug.chroma.API_KEY` for lookups; config `acoustid.apikey` is only the *submission* key. The harness sets the constant per job and restores it afterwards.
* `ACOUSTID_API_KEY` was **not set** in this environment, so lookups used beets' built-in application key (logged redacted as `1v…En`). The code uses your key automatically once the variable is set; **criterion "lookup with your key" is still open.**
* 30 s generated tone → `status: ok`, 0 results (valid, empty). Real files: 52 requests, 52 `ok`, 51 with a fingerprint match, **only 26 linked to a MusicBrainz recording**.

### 1.6 Match quality (real files from `nas_sample/`, AcoustID + MusicBrainz)

| Unit | Files | AcoustID-linked | Class | Top candidate | Distance | Correct? |
|---|---|---|---|---|---|---|
| 郑绪岚《情系红楼梦》FLAC | 11 | 0/11 | needs review | 夢現列車 / 豆屋 | 0.72 | **No** |
| HIFI年代 怀念战友 (1 APE + CUE) | 1 | 0/1 | needs review | an unrelated release (differs run to run) | 0.90–0.95 | **No** |
| Phil Collins *Love Songs* (2 CDs) | 25 | 25/25 | needs review (`medium`) | Love Songs (A Compilation… Old and New) | 0.21 | **Yes** |
| 童丽 loose singles (per track) | 15 | 1/15 | 15 × needs review | other artists' recordings with similar titles (e.g. Greensleeves → John Coltrane) | 0.28–0.70 | none verifiably correct |

"Confident" = beets `strong` (< 0.04). In practice **the only usable automatic result was the Western album**; for the Chinese material the *right* answer is effectively "no candidates", but beets always returns *something*, so distance alone would mislead the user.

Why (from the evidence, not guesswork):
1. AcoustID recognises the Chinese tracks (scores 0.97–0.998) but **their fingerprints are not linked to any MusicBrainz recording** — MusicBrainz coverage, not fingerprinting, is the bottleneck.
2. The 郑绪岚 FLACs have **broken embedded tags** (`artist` = `红豆曲`, a song title; no album) — text search starts from garbage.
3. The APE album is **one image file + CUE**; beets sees 1 item vs 12-track releases.

## 3. M0b — safe copy and tag (`scripts/m0b_v2.py`)

Evidence: `results/m0b_local_result.json`. Destination: `m0/scratch/dest_local` (APFS). Journal: local JSONL, fsync per record. Sources: APFS clones of 3 real NAS-sample files (FLAC, MP3, FLAC). `nas_sample/` is only read.

### Publish probe (local APFS)
| Primitive | Result |
|---|---|
| `renamex_np(…, RENAME_EXCL)` onto existing | refused `EEXIST`, existing untouched, inode preserved |
| `link()` onto existing | refused `EEXIST`, existing untouched |
| `O_CREAT|O_EXCL` onto existing | refused `EEXIST` |
| plain `rename()` | **silently overwrites** (must never be used to publish) |

### Protocol
`intent` (journalled before touching the destination: source size/mtime/SHA-256/audio hash, final path, temp path, pre-existing final's hash) → copy into `.<stem>.mlt-<run>-<op>.tmp.<ext>` in the destination dir (`O_EXCL`) while hashing → verify source unchanged → `copied` → edit tags → `tagged` (temp SHA, audio hash, **inode**) → publish with no-overwrite primitive → `published` (final inode + SHA) → `committed` (ownership `created`).
Recovery inspects the journal **and the files**, never memory: partial temps are discarded; a final with our SHA **and** our inode is ours; our SHA with a different inode → `ownership_uncertain`; a different file → `conflict`.

### Crash injection — 3 files, crash during file 2, then recover + rerun
Crash = `os._exit(137)` in a forked child (like `kill -9`). Both publish methods tested.

| Crash point | renamex | link+unlink | Recovery decision for the crashed op |
|---|---|---|---|
| after intent | pass | pass | restart from intent |
| mid-copy (after 2 × 256 KB) | pass | pass | partial temp discarded, restart |
| after copy | pass | pass | temp discarded, restart |
| after tag | pass | pass | temp intact → resume at publish |
| after link, before unlink | n/a | pass | final is ours (inode+SHA) → published, temp removed |
| after publish syscall, before journal | pass | pass | final is ours (inode+SHA) → published |
| after `published`, before commit | pass | pass | commit |

Every case: all 3 ops terminal and `created`, no temp files, no half-written finals, no duplicate names.

### Awkward situations
| Situation | Result |
|---|---|
| File appears at destination between tag and publish | **conflict**, intruder untouched, our temp removed |
| Source modified *during* the copy (between chunk 1 and 2) | **aborted_source_changed**, nothing published, temp removed |
| Share drops mid-copy | **simulated only** (`ENOTCONN` raised in the write path, process exits); recovery from files alone → op completes `created`, no temp |
| Identical file already at destination | **already_existed**; Undo leaves it untouched |
| Output edited before Undo | Undo **refuses** that file (SHA/inode changed) and removes the unedited one |
| Crash after publish, then Undo | recovered op classed `created` and removed; pre-existing file classed `already_existed` and kept |

Note: a tag-only user edit keeps the *audio* hash identical — **edit detection must use full-file SHA + inode**, the audio hash is only for "same recording" decisions.

### Tag preservation (edit title only, diff every field)
Fixtures carry: custom tag, MusicBrainz ID, 2 artists (multi-value), album artist, embedded JPEG front cover, Chinese lyrics, ReplayGain track gain/peak + album gain, Chinese text. MP3 = ID3v2.4, FLAC = Vorbis + PICTURE, APE = APEv2 on a copy of the real NAS APE (no APE encoder exists here).

| Format | Fields | mutagen (minimal edit) | beets `mediafile` |
|---|---|---|---|
| MP3 | 16 | only TIT2 changed, audio unchanged | same; stays ID3v2.4 |
| FLAC | 15 | only TITLE changed, audio unchanged | same |
| APE | 12 + key list | only Title changed, audio unchanged | value correct, but key respelled `Title` → `TITLE` |

### Audio-only verification (ffmpeg-decoded PCM SHA-256)
MP3, FLAC, APE: deterministic; unchanged after a tag edit that changed the file SHA; changed after an audio edit (MP3/FLAC re-encoded at volume 0.99; APE one byte flipped — still decodes, different PCM). FLAC also carries a STREAMINFO MD5 that could be a cheap pre-check.

### Side files
* **Real GBK cue** (from the APE album): encoding detected as GB18030, `FILE` reference rewritten to the renamed `.ape`, written back **in GB18030** (naive UTF-8 read is still mojibake), reference resolves. Rewritten on a staged copy *before* publish (published files are never rewritten in place).
* **.lrc** published under the track's new stem, byte-identical, nothing left at the old stem.
* **UTF-8 cue**: rewritten pre-publish, reference resolves.

## 4. Findings (things that would have bitten the real tool)

* **F1 — beets leaks state between jobs in one process** unless ~10 private globals are reset (§1.1).
* **F2 — `Library(":memory:")` writes files.** With default config it creates 11 SQLite backups `:memory:-before-<table>-<migration>.bak` (53 KB each) in the CWD and prints to stdout. Requires `create_backup_before_migrations: no`. Python's audit hook did **not** see these writes (SQLite backup API is C) — directory snapshots are needed to catch this class of problem.
* **F3 — MusicBrainz search is not reproducible** between calls. Plans must be built from **persisted** candidates, never by re-running the search.
* **F4 — AcoustID knows the Chinese tracks but MusicBrainz doesn't.** Fingerprinting alone can't produce candidates for most of the collection.
* **F5 — embedded tags can be actively wrong** (artist = song title), not just missing.
* **F6 — APE + CUE "image" albums can't be matched by beets as-is**; the cue sheet is the track list and is GBK-encoded.
* **F7 — plain `rename()` overwrites**; only `RENAME_EXCL` / `link()` / `O_EXCL` are safe.
* **F8 — audio hash cannot detect tag edits**; ownership/undo needs full SHA + inode.
* **F9 — `mediafile` respells APE keys** (case only).
* Carried over: folders named like files (`XXX.flac/`) exist — the walker uses `isfile()`, never the extension.

## 5. What did not happen, and why

| Item | Status | Needed from you |
|---|---|---|
| M0b on the NAS scratch folder | **Not run.** The brief's `<NAS_SCRATCH_PATH>` placeholder was never filled in, and the NAS is still mounted as guest, read-only. | A scratch folder path on the share + a read-write mount of it (DSM account auth was failing in the previous session). Then: `./venv/bin/python -I scripts/m0b_v2.py --dest <path> --label nas` |
| Real SMB drop mid-copy | Simulated only | On the NAS run: start a large copy and disconnect Wi-Fi / unmount; recovery must work from the files |
| NAS publish primitives | Unknown | The probe in the NAS run answers it. Risks: macOS smbfs may not support `RENAME_EXCL` or hard links, and SMB inode numbers may not be stable (would push recoveries to `ownership_uncertain` instead of `created`) |
| AcoustID with your key | Not done | `export ACOUSTID_API_KEY=…` and re-run `scripts/m0a_v2.py` |
| APE encoding fixtures | No `mac` CLI (no Homebrew formula), ffmpeg can only decode APE | Accept using the real APE (done), or I can build Monkey's Audio from source if you want synthetic APE |
| Sample folder list | `<SAMPLE_FOLDER_LIST>` not given | Used the 4 folders already in `m0/nas_sample/`. Still missing from the spec's list: Big5/GBK *mojibake tags*, Traditional Chinese release, live/remaster, an `.lrc` in the real collection |
| Incident | During this session I ran one `touch` at the root of the read-only NAS mount to check writability; it failed with "Operation not permitted", nothing was written. Not repeated. | — |

## 6. Architecture decisions (proposed)

1. **Use beets as a library**, pinned to 2.14.1, behind a single harness module. **Run each matching job in a short-lived worker process** (simplest isolation; the in-process reset also works and stays as defence). Re-run the leak test on any beets upgrade.
2. Always construct `Library(":memory:", set_music_dir=False)` with `create_backup_before_migrations: no`, `BEETSDIR` = empty temp dir, `config.read(user=False)`.
3. **Persist candidates** (the SQLite schema above) and build plans only from persisted data.
4. **Review is the main path, not the exception.** Classify with more than beets' recommendation: e.g. "medium + all tracks AcoustID-linked" is near-certain (Phil Collins), while "no AcoustID link + distance > 0.5" should be shown as *no real match* rather than a bad candidate.
5. Before matching, add a **pre-processing stage**: cue-sheet parsing with encoding detection for image albums; tag-sanity checks (artist that equals a track title, missing album) with folder/filename-derived fallbacks for the search terms.
6. **Evaluate extra metadata sources in M1** for Chinese releases (beets ships Discogs/Deezer/Spotify plugins); MusicBrainz alone will leave most of the collection unmatched.
7. **Apply = journal-first protocol as implemented**: journal on local disk, temps in the destination dir with the `.mlt-` naming, publish with `RENAME_EXCL` → fallback `link()+unlink()` → if neither, **refuse to apply** to that destination (decide after the NAS probe).
8. Ownership classes `created / already_existed / ownership_uncertain`; **Undo only touches `created`**, and only if full SHA + inode still match.
9. Write tags with **minimal mutagen edits** (only changed fields) — preserves APE key spelling and everything else; `mediafile` is acceptable for MP3/FLAC.
10. Side files: `.lrc` is its own op keyed off the new stem; `.cue` is rewritten on a staged copy before publish, in its original encoding.

## 7. Reproduce

```bash
cd ~/Projects/MP3LibraryTool/m0
./venv/bin/python -I scripts/m0a_v2.py                              # ~8 min (MusicBrainz rate limit)
./venv/bin/python -I scripts/m0b_v2.py --dest scratch/dest_local    # ~2 min
```
The first-generation scripts (`m0a_beets_spike.py`, `m0b_nas_safety_spike.py`) are kept for history; their results are superseded.
