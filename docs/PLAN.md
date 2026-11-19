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
| **M2** Recognition and review | background matching queue; review UI; destination preview; conflicts; **desktop window (pywebview)** | 🔄 read-only UI done |
| **M3** Apply, verify, recover | saved plans; copy → tag → verify → publish; journal; resume; undo | protocol proven in M0 |
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

## M2 backlog (original order)

1. ✅ `serve` + read-only inventory browser (three-pane, per the Review & Plan mockups)
2. Recognition queue: rate-limited (MusicBrainz ~1/s, AcoustID 3/s), resumable, cached; one worker process per job (ADR-level finding F1)
3. Pre-processing before matching: GBK/Big5 tag repair; cue-sheet albums; tag-sanity fallbacks
4. Own confidence rule (AcoustID-linked + distance < ~0.15 → Ready); artist agreement for singles
5. Extra candidate sources for mainland releases (evaluate Discogs, Deezer)
6. Review actions: candidates with evidence, Accept / Edit / Skip / Search again; decisions saved immediately
7. Destination preview from the naming template; casefold/NFC collision and existing-file conflicts
8. **Desktop window with pywebview + native folder picker** — [ADR 0001](adr/0001-desktop-shell-pywebview.md)

## Decisions

| Date | Decision | Record |
|---|---|---|
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
