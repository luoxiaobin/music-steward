# Music Steward — Plan

*Formerly MP3LibraryTool (renamed 2026-11-19).*

Living plan. Sources: the Architecture Assessment and M0 Experiments specs (Nov 18, 2026), `m0/EVIDENCE_REPORT.md`, and the decisions below.

## Principles

* Sources are read-only; changes go only to organized copies, from a saved plan.
* Every apply is journalled: crash-safe, resumable, undoable (only what the tool created).
* Local, free, offline except recognition. Never commit real music.

## Milestones

| | Delivers | Status |
|---|---|---|
| **M0** Experiments | beets as a library; safe copy/tag on the NAS | ✅ done — `m0/EVIDENCE_REPORT.md` |
| **M1** Inventory and audit | `scan` / `hash` / `audit` / `inspect`; incremental, crash-safe, read-only; folder-level scan and hash | ✅ done — full NAS inventory in the app DB |
| **M2** Recognition and review | background matching queue; review UI; destination preview; conflicts; **desktop window (pywebview)** | 🔄 review, own info, bulk actions, plan screen done; resumability at scale in test; desktop window and singles rules to do |
| **M3** Apply, verify, recover | saved plans; copy → tag → verify → publish; journal; resume; undo | 🔄 works to a local folder (plan, apply, resume, undo; cue images, disc sets); applying to the NAS to do |
| **M4** Apple export | FLAC/APE → ALAC tree for iPhone; MP3 copied | later |

## Next: thin end-to-end slice (decided 2026-11-19)

Get one album all the way through before widening any step:

1. ✅ Matching queue: "Recognize folder" → units (albums; CD 1/CD 2 merged; singles folders split) → candidates with evidence; resumable; one worker process per unit
2. ✅ Review: candidates with evidence (incl. fingerprints agreeing), track mapping, Accept / Skip / Undo decision
3. ✅ Plan + apply + resume + undo to a local folder (M0 protocol in the app; method probed per destination)
4. ✅ Real test (2026-11-19, local sample): 24 units recognized in 3 m 40 s, 0 errors; 3 accepted
   (劉德華, 林子祥 with garbled tags, Phil Collins 2×CD) → 53 copies in 10 s, sources byte-identical,
   林子祥 tags repaired to Unicode from MusicBrainz, audio identical 17/17

5. ✅ Garbled GBK/Big5 tags repaired in memory before matching (林子祥: distance 0.49 → 0.17)
6. ✅ Plan screen in the UI (mockup "Review plan"): destination chooser, summary cards, blocked items,
   tag and path changes, side files, runs with Apply / Undo run / Resume; destination may never
   overlap a source root. Tested in the browser: apply 36 files, undo, sources unchanged.

7. ✅ Accepts survive a re-match when the same release (or recording, for a single) is still a
   candidate; otherwise dropped with a notice. Skips always survive; a changed set of files resets.
8. ✅ Cover images from several disc folders: one copy when byte-identical, otherwise both kept
   ("Folder (CD 2).jpg") — never blocked.
9. ✅ Search again: your own artist + album/title, or a pasted MusicBrainz release/recording link.
   Tested: 郑绪岚《情系红楼梦》 is not on MusicBrainz at all → stays "No real match" (case for extra sources).
10. ✅ Edit fields after Accept: album, album artist, year, per-track titles; used for the copies' tags
    and paths; kept when the same release is re-accepted or re-linked after a re-match.
11. ✅ Extra sources: Deezer on by default (no key); Discogs automatically when DISCOGS_TOKEN is set.
    MusicBrainz ids are only written for MusicBrainz releases. Measured on the sample with Deezer:
    1 of 24 improved (张也 万事如意: right singer instead of 龚玥), 0 worse; Deezer has the mainland
    artists but not these exact albums. Discogs (with token), measured on the sample: 39 of 179
    candidates; 彩虹之上 → 蔡幸娟 (plausibly right), 童丽 Endless Love only via Search again, 郑绪岚 not
    found; a Discogs copy of Phil Collins' release tied on distance and pushed it out of Ready →
    fixed by ranking fingerprint-confirmed candidates first on near-ties.

Then widen by value: garbled-tag repair before matching → own confidence rule → edit/search/conflicts and the full Plan screen → extra sources for mainland releases → cue-sheet albums, singles rules, Simplified/Traditional key → pywebview window.

12. ✅ Cue-sheet image albums (NAS: ~1,714 — 1,567 APE, 305 FLAC images): cue parsed with encoding
    detection; one virtual track per cue entry (title + length from INDEX times, fingerprint from its
    own slice); matched like any album; apply copies the image (album tags) and a rewritten cue
    (FILE name, album + per-track titles/performers, original encoding; no splitting). Real test:
    HIFI年代1 · 怀念战友 matched (was "no real match"), applied in 21 s, audio identical, cue still
    GB18030, track performers improved (姜苏 / 伽菲珈而 instead of the label 柏菲), undone.

13. ✅ APE sample from the NAS (40 cue albums of 1,483 in 音乐Ape, every 37th): 19 min, 0 errors.
    ~16 plausible matches, ~24 wrong/not found; fingerprints agreed on 1 of 40 (AcoustID has no
    MusicBrainz links for this repertoire). Tuned the rule on it:
    * **Likely** (no fingerprints): distance ≤ 0.15, every track mapped, none missing → 4 of 40, all
      right; bulk "Accept all Ready + Likely" per folder (decisions only; undoable)
    * **disc of a set**: an image that maps onto one disc of a multi-disc release is not "missing"
      the other discs → needs review ("disc 2 of 3") instead of no match
    * **Weak: title agrees** instead of no match when only the album title agrees (still needs care:
      series titles agree across different singers)
    * cue albums fingerprint at most 4 tracks: 10.3 s/album instead of ~28 (same top results);
      full 音乐Ape ≈ 4 h
14. ✅ Simplified/Traditional (OpenCC, pure Python): title check is script-insensitive (经典老歌 =
    經典老歌). Measured: beets already scores Chinese via pinyin, and a second search in the other
    script returned identical candidates for all 40 sample albums (the services fold scripts) at 3×
    the time → off by default (MLT_SCRIPT_RETRY=1). Queue now records a worker that dies before
    writing its own error (12 transient failures in one run left albums stuck 'queued'/'running';
    all succeeded on re-run).
15. ✅ Organize from the album's own info, for albums no online source knows ("Use own info").
    A 'Local' candidate built from the cue sheet (cue albums) or tags (repaired) → shared file-name
    suffix "(Artist – Album)" → file names → folder name "Artist - Album (1998)"; placeholders
    (未知标题, Unknown Artist) count as missing; download-site noise (`.-.`, `.专辑`, `(APE)`) and
    repeated track numbers are dropped. Accepted like any release (Edit fields, plan, apply, undo);
    never carries MusicBrainz IDs; never counts toward confidence; kept across re-matches
    (release_key 'local'). On the 23 Weak/No-match NAS APE albums it gives a usable album,
    artist and track list for all 23.
16. ✅ Discs of one set and cue track titles. Accepted albums that land in the same album folder
    are discs of one set: files are named "2-01 - Title", cue images "Album (CD 2).ape" + cue, and
    the disc tag is "2/N" (N = the highest disc seen, the release's disc count, or the user's
    value). Own info reads the disc from a trailing "CD2"/"Disc 2"/"(CD 2)" in the album title or
    a "CD 2" folder; Edit fields has "Disc _ of _" and, for cue albums, every track title.
    Two different albums that would share a folder still show as name conflicts, never merge silently.
17. ✅ Folder-wide bulk actions on undecided albums (decisions already made are never touched):
    "Add own info to No match + Weak", "Accept own info" (after scanning the "Own info:" line each
    row now shows), "Skip No match + Weak"; plus the existing "Accept all Ready + Likely".
18. ✅ Lookup cache (`~/.music-steward/http-cache.sqlite`, `music-steward cache [--clear]`): every
    web answer (MusicBrainz, Deezer, Discogs, AcoustID) kept 30 days (MLT_CACHE_DAYS), keyed by a
    hash so keys/tokens are never stored; audio fingerprints kept by file path + size + mtime
    (+ cue slice), so a known slice is not decoded again. Cache hits skip the services' waits;
    real requests are throttled (Deezer <= ~6/s, Discogs <= 1/s). Found while profiling: a Discogs
    joke release "Unicode" (274,000-character track titles) cost 13 s of CPU per album that drew it
    → comparisons bounded to 300 characters, stored text to 500. Measured on 3 NAS APE albums:
    cold 10-15 s → fully warm 1.4-1.8 s each, identical candidates and distances.
19. ✅ Resumability at scale (2026-11-19, `tools/resume_test.py`). Recognition of 329 NAS APE
    albums (every 5th of 音乐Ape): Ctrl-C after 60, hard kill after 60 more, resumed to the end →
    0 albums redone, both interrupted albums finished on resume, 0 left stuck; ~11 s/album.
    Result mix: 32 Likely, 89 Needs review, 48 Weak, 151 No match. Apply of 40 of them (123 files,
    14 GB) to a local folder, on a copy of the DB: Ctrl-C mid-copy, hard kill mid-copy, resumed →
    temp files recovered, nothing half-written published; 123 sources unchanged; undo removed all
    123 copies (and left alone what the run had not created).
    Found and fixed on the way: 9 albums failed to match and 38 copies failed tagging because
    their APE tags hold GBK bytes or corrupt items (or an ID3 block precedes the APE audio) →
    lenient tag reading (repair proposals for GBK/Big5), beets falls back to it, and a copy's
    unreadable APE tag is rebuilt from its salvageable items (audio verified identical).
    Follow-up: when an album tag is a placeholder (未知标题), search with the folder name instead.
20. ✅ Subset trial, end to end (2026-11-19): 357 音乐Ape albums recognized → user review in the app
    (bulk + selection) → 135 albums accepted → plan → apply to a local folder → checked by the user
    in foobar2000 (Mac). Trial 1: 85 min, 326/327 files. Found and fixed: GB18030 cues unreadable
    on the Mac (→ UTF-8 + BOM), one artist in two folders (George Lam / 林子祥 → the album's own
    Chinese name wins), byte-identical duplicate albums (→ copied once), genre prefixes, mid-title
    "(CD5)", own-info track performers, WAV tagging, the browser pane silently blocking confirm()
    (→ in-page dialogs), DB setup locking concurrent requests, 37 wrong "title differs" accepts
    (→ own info), and APE verification decoding twice (→ exact audio-byte check, 8× faster).
    Trial 2: 10 min, 331/331 files, 0 blocked, 126 cues UTF-8; albums of separate track files now
    get a generated album cue (user request). User verdict: "it worked".
21. ✅ End-to-end polish after the trial (2026-11-20), each tried by the user in the app:
    review safety ("title differs" marker/banner; bulk accept offers only the agreeing ones), keyboard
    review with a pinned action bar; plan screen "Done for you" + folder-tree preview; apply progress
    with ETA and Stop (resumable); Settings panel (names, year, cue encoding, album cues, catalogues,
    destination); desktop window (`music-steward app`, native folder picker); resizable panels with
    auto-size, Columns menu, panel tones; listening while reviewing (APE / cue-track previews).
    Then: search (albums/folders/tracks, script-insensitive, pinyin) and "Filter this list"; interface in
    English, 简体中文, 繁體中文 (OpenCC Taiwan phrasing), Français (Canada), Español (EE. UU.), chosen in Settings.
    Still on the list: apply to the NAS, a double-clickable signed app.

## M2 backlog (original order)

1. ✅ `serve` + read-only inventory browser (three-pane, per the Review & Plan mockups)
2. Recognition queue: ✅ resumable, one process per unit; ✅ lookup + fingerprint cache, Deezer/Discogs throttled; ✅ resumability proven at scale (item 19); one worker process per job (ADR-level finding F1)
3. ✅ Pre-processing before matching: GBK/Big5 tag repair; cue-sheet albums; tag-sanity fallbacks
4. ✅ Own confidence rule (AcoustID-linked + distance < ~0.15 → Ready); artist agreement for singles (still to do)
5. ✅ Extra candidate sources for mainland releases (evaluate Discogs, Deezer)
6. ✅ Review actions: candidates with evidence, Accept / Edit / Skip / Search again; decisions saved immediately
7. ✅ Destination preview from the naming template; casefold/NFC collision and existing-file conflicts
8. **Desktop window with pywebview + native folder picker** — [ADR 0001](adr/0001-desktop-shell-pywebview.md)

## Future features (after M2/M3; see `docs/FEATURE_SHEET.md`)

Chinese UI → macOS app (notarized) → NAS/Docker mode + phone-friendly review over the home network
(covers iPhone and Android) → Windows desktop → tag repair for more scripts (Japanese, Korean,
Cyrillic) on demand → Apple export (M4) → native phone apps only if a store launch becomes a goal
(GPL vs App Store and service terms to settle first).

## Decisions

| Date | Decision | Record |
|---|---|---|
| 2026-11-19 | **Names in the copies are written in Simplified Chinese** (artist, album artist, album; also cue track performers), so one artist never lands in two folders (鄧麗君 / 邓丽君). Track titles keep their spelling; the user's own edits are kept as typed. `MLT_SCRIPT=traditional|keep` changes it | this file |
| 2026-11-19 | **Original release year** for folder names and tags (MusicBrainz first release, Discogs master), else the matched release's year; an edited year wins | this file |
| 2026-11-18 | NAS publish method: `claim_rename` (O_EXCL placeholder, then rename); local disk keeps `RENAME_EXCL` | `m0/EVIDENCE_REPORT.md` §6.7 |
| 2026-11-18 | Desktop shell: pywebview at the end of M2; not Electron | [ADR 0001](adr/0001-desktop-shell-pywebview.md) |
| 2026-11-18 | License GPL-3.0-or-later | `LICENSE` |
| 2026-11-19 | **No full-collection recognition until testing is solid**: sample results reviewed by the user, confidence rule tuned on them, resumability proven at scale, bulk review actions in place | this file |
| 2026-11-19 | **Renamed to Music Steward** (package `music_steward`, command `music-steward` / `steward`; `music-steward` free on PyPI; `steward` alone is taken). Data folder `~/.music-steward`, migrated automatically | this file |
| 2026-11-19 | Thin end-to-end slice first; first apply destination is a **local folder** (NAS later) | this file |
| 2026-11-18 | **No whole-NAS hashing.** Hash per selected folder (CLI `hash <folder>`, UI "Hash folder"); re-scan per folder too | this file |

## Scale (measured on the NAS, 2026-11-18)

| | |
|---|---|
| Files / audio files | 156,763 / 142,148 |
| Audio size | ~3.0 TB |
| Codecs | MP3 71k · FLAC 66k · APE 2.8k · WMA 1.1k · MP2 0.7k · DTS 86 · WAV 46 |
| Garbled (legacy-encoded) tags | 43,488 files — mostly MP3 (42k of 72k) |
| Missing title / artist / album | 34,609 |
| Folders named like files · cue sheets · lyrics | 3,464 · 2,483 · 3,964 |
| Unreadable | 21 (was 227: 206 APE files with an ID3 prefix or a `.mp3` name recovered) |
| Wrong extension | 89 (58 `.ape` that are MP2, 22 `.mp3` that are APE, 6 VCD videos, …) |

| Operation | Measured | Assessment target | Consequence |
|---|---|---|---|
| First full scan (header reads) | 75 min @ ~46 files/s, 24 workers | < 15 min | one-off; acceptable |
| Re-scan of whole share, nothing changed | **13.4 min** (listing ~10k folders over SMB) | < 1 min | **re-scan per folder** instead |
| Re-scan of one large folder (音乐Ape, 6.9k files) | 75 s | — | the normal workflow |
| Hashing everything | ~3 TB ⇒ ≥ 8–9 h over gigabit | — | **per folder only** (decision above) |
| Recognition of everything | MusicBrainz ~1 req/s ⇒ days | < 1 h | recognize per selection; long jobs resumable |
