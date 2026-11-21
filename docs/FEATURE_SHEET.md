# Music Steward — feature sheet

*A careful steward for a large, messy music collection.* Status: working end to end on real data (macOS desktop window or local web UI; 135-album trial checked by the owner in foobar2000); not yet packaged as a signed app. GPL-3.0-or-later.

## The problem
Collections built over 20+ years (CD rips, downloads, image + cue albums) sit on a NAS with broken text, missing tags and folder names as the only metadata. On the reference collection (~3 TB, 142k audio files): **43k files with garbled GBK/Big5 tags**, 35k missing title/artist/album, 1,700 single-file "image" albums with cue sheets, 89 files with the wrong extension. General-purpose taggers (MusicBrainz Picard, beets, Mp3tag) are built to edit files **in place**; on a collection that cannot be replaced, the owner wanted an organized *copy* instead, and on this collection online catalogues knew only part of the albums (on a 40-album APE sample, about 40% had no real match anywhere).

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
| **Review** | Candidates with evidence (distance, fingerprints agreeing, missing tracks, track mapping), a "title differs" warning, **listen to any track** (APE and single cue tracks converted on demand), Accept / Own info / Skip / Undo with keyboard shortcuts, **Search again** (terms or a MusicBrainz link), **Edit fields** (album, artist, year, disc, every track title). |
| **Find** | **Search** the whole collection (albums, folders, tracks) by any part of a name, Simplified/Traditional-insensitive, by **pinyin** (`denglijun`, `dlj`) and by repaired tag text; **Filter this list** narrows the current folder. |
| **Own info** | For albums no catalogue knows (~40% of the Chinese APE sample): build the release from the cue sheet, repaired tags, file names or folder name, with download-site noise cleaned. |
| **Bulk** | Accept all Ready + Likely; add / accept own info for No match + Weak; skip the rest — undecided albums only; or tick albums (filter by status, "title agrees") and accept / own info / skip / clear exactly those. |
| **Plan** | Frozen, previewable plan with a **folder-tree preview** and a "Done for you" list: names in Simplified (one folder per artist), original release year, the album's own artist name over Latin/junk catalogue names, identical duplicate albums copied once, a cue sheet for every album (UTF-8), side files (covers, lyrics); paths Windows-safe and NFC; conflicts flagged, never resolved by overwriting. |
| **Apply / undo** | Copy → tag the copy → verify (exact audio bytes) → publish, with a journal; progress with ETA and **Stop**; crash-safe resume; undo removes only copies that are unchanged since. ~70 MB/s from the NAS. |
| **Settings & languages** | Naming rules, year, cue encoding, album cues, catalogues, default destination; interface in English, 简体中文, 繁體中文, Français (Canada), Español (EE. UU.). |

## Key design highlights
1. **Sources are never modified.** All changes go to organized copies; the destination may never overlap a source; every trial checked that all sources were unchanged afterwards.
2. **Crash-safe by construction.** Journal-first apply; no-overwrite publish probed per destination (atomic on a local disk; on SMB shares claim-then-rename, which has a documented residual window — writing the copy to a NAS is experimental, see [Recovery](RECOVERY.md#writing-the-organized-copy-to-a-nas)); scans abort, not "mark removed", when a share drops; matching resumable unit by unit (each in its own process).
3. **Built for non-Western collections.** Legacy-encoding repair with CJK heuristics (tags, cue sheets, unreadable APE tags), GB18030/Big5 cue sheets read correctly and written as UTF-8 for any player, Simplified/Traditional keys, pinyin search, extra catalogues, own-info fallback.
4. **Evidence over automation.** Nothing is accepted silently; bulk actions are limited to measured-safe classes; every decision is reversible until apply, and apply is reversible after.
5. **Fast on a NAS.** Per-folder scan and hash; lookup + fingerprint cache (re-match ~1.5 s/album vs 10–15 s); rate limits respected (MusicBrainz 1/s, Deezer, Discogs).
6. **Plays in music servers (required).** The organized copy works in Subsonic-standard servers (Navidrome, and the
   phone apps that speak the Subsonic protocol): every album once, all tracks in order, correct titles, artists,
   years and durations — checked automatically against Navidrome on every pull request. Cue-sheet albums need the
   setting "one file per track" for this (servers do not read cue sheets); any audio format works, since the server
   converts what a phone cannot play (APE included).
7. **Private and local.** Runs on your computer: an app process with the UI on 127.0.0.1 (one-time token, CSP, Host check), one short-lived worker process per album while recognizing, and FFmpeg/Chromaprint as external tools; keys only from the environment, never stored. What is sent online: [Configuration](CONFIGURATION.md#what-is-sent-to-online-services).

## Architecture in one line
Python 3.12 engine (beets 2.14 in-process for matching, mutagen for tags, FFmpeg for probing/hashing/decoding/previews) + SQLite app database + plain HTML/JS UI, in a pywebview desktop window (`music-steward app`, [ADR 0001](adr/0001-desktop-shell-pywebview.md)) or a local browser tab.

## Measured (reference collection)
Full scan 75 min once, folder re-scan ~75 s for 6.9k files; recognition ~10–13 s/album cold, ~1.5 s warm; resumable at scale (329 albums, Ctrl-C and hard kill: 0 redone, 0 stuck); apply ~70 MB/s (135 albums, 43 GB in 10 min); search ~100–150 ms over 142k tracks; 170 automated tests.

## Not done yet
Signed, double-clickable macOS app · writing the organized copy to a NAS share (experimental: claim-then-rename has a residual window) · singles rules (loose tracks, live/cover versions) · own-info rule for credit lines used as artist (`【指挥】…【朗诵】…`) · native-speaker review of the French and Spanish (and Chinese) wording · Apple (ALAC) export · Windows testing.

## Future features (proposed order)
Guiding constraint: the heavy work (scanning terabytes, decoding APE, fingerprinting) belongs on a computer or on the NAS, not on a phone.
1. ✅ **Interface languages** — English, 简体中文, 繁體中文, Français (Canada), Español (EE. UU.), chosen in Settings; plus search with pinyin.
2. **macOS app** — ✅ desktop window (pywebview); still to do: bundled Python + FFmpeg, signed and notarized "Music Steward.app". *~1 week.*
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
* **License:** the app is GPL-3.0-or-later and uses mutagen (GPL-2.0-or-later), beets (MIT) and FFmpeg (LGPL/GPL depending on the build); GPL and the Mac App Store terms are widely considered incompatible (VLC was removed for this). Options: distribute outside the store (notarized download, Homebrew), or relicense and replace the GPL components — a major change.
* **Service terms:** AcoustID's free web service is for non-commercial use (paid plans exist); Deezer and Discogs API terms need checking for a commercial or widely distributed app; MusicBrainz asks for an identifying User-Agent and may need a paid live feed at scale.
* **Sandbox:** App Store apps must use security-scoped bookmarks for NAS/user folders and bundle FFmpeg and Python; feasible but real work.
* **Audience:** the strongest pitch is what the trials demonstrated — CJK collections on a NAS, organized into a reviewed copy while the originals are only read.
