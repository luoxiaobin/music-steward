# Music Steward — feature sheet

*A careful steward for a large, messy music collection.* Status: working end to end on real data (macOS, local web UI); not yet packaged. GPL-3.0-or-later.

## The problem
Collections built over 20+ years (CD rips, downloads, image + cue albums) sit on a NAS with broken text, missing tags and folder names as the only metadata. On the reference collection (~3 TB, 142k audio files): **43k files with garbled GBK/Big5 tags**, 35k missing title/artist/album, 1,700 single-file "image" albums with cue sheets, 89 files with the wrong extension. Existing taggers (MusicBrainz Picard, beets, Mp3tag) rewrite files **in place**, assume Western catalogues, and are hard to trust on a collection that cannot be replaced.

## Who it is for
* Owners of large personal collections, especially **Chinese / East Asian** music, where tag encodings are broken and online catalogues are thin.
* People who keep their music on a **NAS** and want an organized copy (e.g. for a player or an Apple device) without risking the originals.
* Not for: streaming-only users; small, already-tidy libraries.

## What it does — scan → recognize → review → plan → apply
| Step | Highlights |
|---|---|
| **Scan / audit** | Read-only inventory over SMB; incremental (size + mtime); per-folder re-scan in seconds; finds unreadable files, wrong extensions, duplicates (tag-independent audio hash), garbled tags, orphan lyrics, cue sheets. |
| **Recognize** | Albums, discs of a set and cue image albums as units; MusicBrainz + AcoustID fingerprints, plus Deezer and Discogs; garbled tags repaired *in memory* before searching; Simplified/Traditional-insensitive title checks. |
| **Confidence** | Own rule, tuned on real data: **Ready** (fingerprints confirm) · **Likely** · **Needs review** · **Weak** · **No match**. |
| **Review** | Candidates with evidence (distance, fingerprints agreeing, missing tracks, track mapping), audio preview, Accept / Skip / Undo, **Search again** (terms or a MusicBrainz link), **Edit fields** (album, artist, year, disc, every track title). |
| **Own info** | For albums no catalogue knows (~40% of the Chinese APE sample): build the release from the cue sheet, repaired tags, file names or folder name, with download-site noise cleaned. |
| **Bulk** | Accept all Ready + Likely; add / accept own info for No match + Weak; skip the rest — undecided albums only. |
| **Plan** | Frozen, previewable plan: destination paths from naming rules (Windows-safe, NFC), tag changes, side files (covers, lyrics, rewritten cue in its original encoding), conflicts flagged, never resolved by overwriting. |
| **Apply / undo** | Copy → tag the copy → verify → publish, with a journal; crash-safe resume; undo removes only copies that are unchanged since. |

## Key design highlights
1. **Sources are never modified.** All changes go to organized copies; the destination may never overlap a source; verified byte-identical sources after every test run.
2. **Crash-safe by construction.** Journal-first apply; no-overwrite publish probed per destination (SMB has no atomic exclusive rename → claim-then-rename); scans abort, not "mark removed", when a share drops; matching resumable unit by unit (each in its own process).
3. **Built for non-Western collections.** Legacy-encoding repair with CJK heuristics, cue sheets in GB18030/Big5 kept in their encoding, Simplified/Traditional keys, extra catalogues, own-info fallback.
4. **Evidence over automation.** Nothing is accepted silently; bulk actions are limited to measured-safe classes; every decision is reversible until apply, and apply is reversible after.
5. **Fast on a NAS.** Per-folder scan and hash; lookup + fingerprint cache (re-match ~1.5 s/album vs 10–15 s); rate limits respected (MusicBrainz 1/s, Deezer, Discogs).
6. **Private and local.** One Python process; UI on 127.0.0.1 with a one-time token, CSP, Host check; keys only from the environment, never stored in the clear.

## Architecture in one line
Python 3.12 engine (beets 2.14 in-process for matching, mutagen for tags, FFmpeg for probing/hashing/decoding) + SQLite app database + plain HTML/JS UI served locally; desktop window via pywebview planned ([ADR 0001](adr/0001-desktop-shell-pywebview.md)).

## Measured (reference collection)
Full scan 75 min once, folder re-scan ~75 s for 6.9k files; recognition ~10–13 s/album cold, ~1.5 s warm; 142 automated tests.

## Not done yet
Desktop window and installer · apply to the NAS (protocol proven, UI targets a local folder) · singles rules · APE playback in the browser · Apple (ALAC) export · Windows testing.

## Future features (proposed order)
Guiding constraint: the heavy work (scanning terabytes, decoding APE, fingerprinting) belongs on a computer or on the NAS, not on a phone.
1. ✅ **Interface languages** — English, 简体中文, 繁體中文, Français (Canada), Español (EE. UU.), chosen in Settings; plus search with pinyin.
2. **macOS app** — pywebview window, bundled Python + FFmpeg, signed and notarized download. *~1 week.*
3. **NAS / Docker mode + phone-friendly review** — run the engine on the NAS (local disk speed instead of SMB: faster scan and hash); every device uses the same web UI over the home network with proper sign-in. A swipe-to-Accept/Skip review with audio preview covers **iPhone and Android** with no store app.
4. **Windows desktop** — UNC paths, the publish-safety probe on NTFS/SMB, WebView2 window, installer, full test pass. *1–2 weeks.*
5. **More collection languages** — tag repair for Japanese (Shift-JIS), Korean (EUC-KR/CP949), Cyrillic (CP1251), each with its own heuristics and test files; added one script at a time, on demand.
6. **Apple export (M4)** — FLAC/APE → ALAC tree for iPhone listening; Android plays the organized FLAC copy directly.
7. **Native iPhone / Android apps** — only if a store launch becomes a goal and the licence questions below are settled; phones suit listening and reviewing, not organizing.

---

## Brief for a functional review
Please review against the user's journey, not just the code: *a cautious owner with a 3 TB NAS wants an organized copy without ever risking the originals.*
* **Safety:** can any path write to a source root, overwrite an existing file, or lose work on interruption (scan, recognize, apply, undo)? See `music_steward/apply/protocol.py`, `plan.py`, `scanner.py`, `recognize/queue.py`.
* **Correctness of recognition decisions:** confidence rule (`recognize/results.py`), own-info extraction (`recognize/local.py`), disc-set grouping (`apply/plan.py`).
* **Review UX:** can a user get through 1,500 albums in reasonable time? What is missing (filters, keyboard flow, undo at scale)?
* **Edge cases:** Unicode/NFC/case collisions, Windows-illegal names, cue encodings, multi-disc sets, junk catalogue entries.
* Run: `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/python -m pytest`; UI: `music-steward serve --root <folder> --dest <empty folder>`.

## App Store readiness — open questions (to verify before any launch)
* **License:** the app is GPL-3.0 and embeds beets (GPL) and FFmpeg; GPL and the Mac App Store terms are widely considered incompatible (VLC was removed for this). Options: distribute outside the store (notarized download, Homebrew), or replace GPL components — a major change.
* **Service terms:** AcoustID's free web service is for non-commercial use (paid plans exist); Deezer and Discogs API terms need checking for a commercial or widely distributed app; MusicBrainz asks for an identifying User-Agent and may need a paid live feed at scale.
* **Sandbox:** App Store apps must use security-scoped bookmarks for NAS/user folders and bundle FFmpeg and Python; feasible but real work.
* **Audience:** strongest pitch is the niche no one serves well — CJK collections on a NAS, with zero risk to originals.
