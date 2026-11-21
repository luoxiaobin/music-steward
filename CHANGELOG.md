# Changelog

All notable changes to Music Steward (formerly MP3LibraryTool). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/)
(0.x: the interface and data formats may still change between minor versions).

## [Unreleased]

### Added
- **Setting: albums of one file with a cue sheet → one file per track.** For music servers and phone players that
  don't read cue sheets. Lossless: each track is cut from the image into FLAC at the cue sheet's exact positions
  (sample-exact, the source's bit depth kept), and checked: its decoded samples must equal the same samples of the
  image. The image is never changed. MP3 images stay one file (cutting would mean re-encoding); the plan says so.
  Audio before the first track (a pregap, hidden audio) stays in track 1, so the tracks cover the whole image; an
  album-long lyrics file (`image.lrc`) is split along the tracks with times counted from each track's start.
  Measured on a real 43-minute APE from the NAS: 12 tracks cut and verified in 22 s, joined again identical to the
  image. Default: keep one file + cue sheet, as before.
- **Albums keep their decisions when folders are renamed or moved** (ADR 0002). Every file gets an identity from its
  audio (the audio's length and its first and last 64 KiB, tags excluded; small files such as cue sheets: the whole
  file). A file found at a new path whose content is a known file no longer at its old path is that file, moved:
  it keeps its record, and its album keeps its matches and your decision. Measured on the NAS: about 50 ms per
  file, read only when a file is new or changed; existing files get their identity once, on their next scan.
- **Changed audio goes back to review.** When a file's audio (or an album's cue sheet) changes after you decided,
  the album is queued again, marked "Audio changed · decide again", and left out of plans (listed under "Left out")
  until you decide again. Retagging a file does not count as a change.
- User documentation: a complete install and first-run path in the README, a [User guide](docs/USER_GUIDE.md),
  [Recovery and safety](docs/RECOVERY.md), [Configuration](docs/CONFIGURATION.md) (naming defaults with examples,
  credentials, data locations, what is sent online) and [Architecture](docs/ARCHITECTURE.md). (Codex review)

- **Retry failed** on the plan screen (and `resume(..., retry_failed=True)`): files that failed in a run (drive
  disconnected, disk full) are tried again in the same run, so Undo still covers everything. A **Not copied** list
  shows each file that was not copied and why.

### Fixed
- **Apply could write into the collection through a link.** The destination was only checked when the plan was
  built; a destination folder replaced afterwards by a link into the collection made Apply write copies there. Apply
  and Resume now re-check the destination, and every file's real path is checked before it is written.
- **An interrupted run could finish with files silently missing.** A crash while a run was being prepared left a
  journal with only part of the plan; Resume finished those and reported success. A run is now marked ready only
  when every file is journalled, and Resume adds any that are missing.
- A torn last line in an apply journal (crash mid-write) no longer swallows the next record.
- **Cue sheets with one FILE per disc image** gave every disc the track list of the first: the matcher, track
  mapping, previews and own info now use each image's own tracks, and each disc's copy gets its own cue sheet.
- Documentation accuracy: what the app reads and writes, NAS publishing marked experimental with its residual
  limitation, beets' license (MIT), the process model, milestone status.

### Changed
- CI: GitHub Actions updated to actions/checkout v7 and actions/setup-python v7 (Node.js 20 is deprecated on runners).

## [0.3.0] — 2026-11-19

The first version tried end to end on a real collection: 135 albums recognized, reviewed, planned and copied, then
checked by the owner in foobar2000.

### Added
- **Search** the whole collection (albums, folders, tracks): any part of a name, Simplified/Traditional-insensitive,
  repaired tag text, **pinyin** (`denglijun`, `dlj`); `/` or ⌘K; a "Filter this list" box for the current folder.
- **Interface languages:** English, 简体中文, 繁體中文 (Taiwan phrasing), Français (Canada), Español (EE. UU.).
- **Settings panel:** names (Simplified / Traditional / as released), year (original / release), cue encoding, album
  cue sheets, catalogues, default destination, language.
- **Desktop window** (`music-steward app`, pywebview): signed in automatically, native folder picker, asks before
  quitting during a job.
- **Listen while reviewing:** play any track; APE/WavPack/TTA and single cue tracks are converted on demand.
- **Review speed and safety:** "title differs" warnings, keyboard review (A/O/S/U, arrows), pinned action bar,
  select-and-act on many albums, bulk accept that offers only the agreeing matches.
- **Plan screen:** "Done for you" (artist merges, Simplified, original years, duplicates, album cues) and a
  folder-tree preview; apply progress with ETA and **Stop**.
- **Layout:** resizable panels with auto-size, a Columns menu, distinct panel tones.
- **Naming rules from the trial:** names in Simplified (one folder per artist), original release year, the album's
  own artist name over Latin/junk catalogue names, identical duplicate albums copied once, a cue sheet for every album.
- Lookup and fingerprint cache; Deezer and Discogs throttled.
- `music-steward --version`; this changelog; CI on GitHub.

### Changed
- Copied cue sheets are written as **UTF-8 with a BOM** (GB18030 cues showed as mojibake in foobar2000 for Mac);
  `MLT_CUE_ENCODING=original` keeps the old behaviour.
- Apply verifies APE copies by their exact audio bytes instead of decoding twice: about 8× faster.

### Fixed
- APE files with an ID3 block in front, or APEv2 tags holding GBK bytes or corrupt items, can be read, matched and
  tagged (the copy's tag is rebuilt; audio verified identical).
- WAV cue images can be tagged.
- The embedded browser silently blocked confirmation dialogs: the app now has its own.
- Concurrent web requests no longer lock the database.
- Junk catalogue entries (a 274,000-character "track title") no longer stall matching.

## [0.2.0] — 2026-11-19

### Added
- **Recognition:** albums, discs of a set and cue-sheet image albums as units; MusicBrainz + AcoustID, Deezer,
  Discogs; garbled GBK/Big5 tags repaired in memory before searching; own confidence rule (Ready, Likely, Needs
  review, Weak, No match); resumable queue, one process per album.
- **Review UI:** candidates with evidence, track mapping, Accept / Skip / Undo, Search again, Edit fields.
- **Own info** for albums no catalogue knows: from the cue sheet, tags, file names or folder name.
- **Plan, apply, resume, undo** to a local folder: journal-first copy → tag → verify → publish; no overwrite;
  undo removes only unchanged copies.
- Renamed to **Music Steward**.

## [0.1.0] — 2026-11-18

### Added
- M0 experiments: beets as a library, safe copy-and-tag on the NAS (claim-then-rename publish), evidence report.
- **Inventory and audit** (read-only): incremental scan over SMB, per-folder re-scan, tag-independent audio hashes,
  audit filters (garbled tags, unreadable, wrong extension, duplicates, cue sheets, orphan lyrics …).

[Unreleased]: https://github.com/luoxiaobin/music-steward/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/luoxiaobin/music-steward/releases/tag/v0.3.0
