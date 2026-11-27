# Changelog

All notable changes to Music Steward (formerly MP3LibraryTool). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/)
(0.x: the interface and data formats may still change between minor versions).

## [Unreleased]

### Fixed
- The status line after recognizing or hashing a folder said "Recognized of “folder”"; it now says "Recognized
  “folder”" (and "Hashed “folder”").

## [0.4.0] — 2026-11-27

Highlights: collection folders that move, come and go (Change path, not connected, Add, Hide); History; the
Overview; removing not-found albums; each track's artist; a big collection stays responsive; and `Music Steward.app`,
built and self-tested by CI.

### Added
- **Change path of a collection folder** ([ADR 0004](docs/adr/0004-collection-folders.md)): when the same collection
  is now at another path — the share mounted as `/Volumes/music-1` instead of `/Volumes/music`, a new NAS address —
  **Settings → Collection folders → Change path…** moves it there and keeps every album, decision and edit. The app
  first compares up to 200 known files with the new folder (same relative path, same size; nothing is opened or
  written there) and refuses a folder where fewer than 90% match ("this does not look like the same collection"),
  a folder that is already a collection folder, or one inside or around another. It waits while a job runs, while a
  copy is unfinished, and while originals are still set aside. Afterwards *Retire originals* is off again for that
  folder, a history entry records the change, and the app offers **Scan now**. Command line:
  `music-steward collection list` and `music-steward collection set-path OLD NEW`.
- **The macOS app** (`docs/PACKAGING.md`): `Music Steward.app`, built by CI on Apple silicon, checked by its own
  self-test and kept as a download (added to the release on a version tag). It finds FFmpeg in Homebrew's folders and
  says how to install it when it is missing; it reads the keys (and `MLT_*` settings) from your login shell and keeps
  them in memory only. Signing and notarization run once the Developer ID secrets are added; until then open it the
  first time with right-click → Open. New: `music-steward self-test`.
- **First launch**: `music-steward app` (and the app) open with no collection folder yet and offer **Add a collection
  folder…**, instead of quitting with "no collection yet".
- **Overview**: where a collection folder stands, on one page — files (and their problems), albums (found, waiting for
  you, Ready or Likely, Needs review, No match, accepted, skipped, not found), organized copies (copied and verified,
  not copied yet, failed or unfinished copies) and originals (set aside, archived, deleted) — with **Next**: what to do
  first, in the order of the journey. Each line leads to where you act on it. Read-only; it never opens a file. Command
  line: `music-steward report` (or `--json`). The album list has two new filters to match: *Ready + Likely* and
  *No match + Weak* (and *Not found*).
- **Removing a not-found album.** An album whose files are no longer in its folder shows a *Not found* notice in its
  details: leaving it changes nothing (it clears by itself if the files come back), and **Remove…** removes the app's
  record of it — the album, its decision, its matches and your edits — after a confirmation that says so. No file is
  touched anywhere. *Remove not-found albums (N)…* in the album list does the same for a whole folder. Each album is
  checked again at that moment, the History keeps the removed album's name, and if the files come back later a scan
  finds them as a new album.
- **Add and Hide collection folders** ([ADR 0004](docs/adr/0004-collection-folders.md)). *Settings → Collection
  folders → Add…* registers another folder of music (a second share, a USB drive): nothing is read until you scan it,
  and *Retire originals* is off for it. A folder inside a collection folder is not added (the app offers to open it
  there), and one that contains a collection folder, or overlaps the default destination folder, is refused. **Hide**
  stops showing and scanning a collection folder and keeps everything about it; **Show** (or adding the same path)
  brings it all back. With more than one collection folder, the header has a switcher. Command line:
  `music-steward collection add | hide | show PATH`.
- **History**: one readable list of what changed your copies, your originals or a collection folder's path — each
  copy run, Resume, Retry failed and Undo, every Retire, Restore, Archive and Delete, and every Change path — with the
  time, the albums and the folders involved. **History** in the header shows them all; an album's details show its
  own. Kept on this Mac, never in the way of the action it records, and nothing in it can be changed.
- **Edit fields: each track's artist.** For duets, guest singers and compilations, *Edit fields* now has an
  **Artist** box per track (per cue-sheet track for albums of one file). It is written to that track's tags, to the
  album's cue sheet (PERFORMER), and to the tracks cut from a cue-sheet album, exactly as typed; empty keeps the
  release's value. The album artist, and so the album folder, is unchanged.
- **Not connected**: a collection folder whose drive or share is not mounted is shown as such, with **Check again**
  and **Change path…**; when the same network share is mounted elsewhere, the app suggests that path (it remembers
  the share's host and name on this Mac, never a user name or password). Scan, recognition, copy, retire, restore,
  archive and delete are refused for it; *Undo run* still works (it only touches the destination).
- **Folder check**: wherever you type a folder (Settings → Default destination folder, the plan's Destination) the
  app now tells you, as you type, whether it is written correctly, whether it exists, and whether it can be used —
  and refuses to save or build with one that cannot. It recognises a **network address** such as
  `//nas/music/Ape.Flac` or `smb://nas/music/…` (which no program can open directly), finds the same share if it
  is connected, and offers the path as it appears on this Mac (`/Volumes/music/Ape.Flac`) with a "Use this path"
  button. It also says when a drive is not connected, when the parent folder is missing (and the closest existing
  folder), when it is a file, when the app may not write there, and when it is inside your collection. Settings now
  explains that the destination is where copies are written, not the folder to scan, and lists the collection folders.
  A destination that fails the check is no longer stored first and complained about afterwards.
- **Retire originals** ([ADR 0003](docs/adr/0003-retire-originals.md)): after a copy is verified, set the originals
  aside so you end up with one organized library — optional, off until you allow it per collection folder
  (Settings → Retiring originals). On the plan screen, **Retire originals…** re-checks every album (all files copied;
  the organized copy still there with identical audio, also if you retagged it; the original unchanged; no links),
  shows what can and cannot be set aside and why, and asks for a confirmation that includes the backup warning.
  The originals are **moved, never deleted**, into `_ORIGINALS/run…/` in the same collection folder (same drive,
  instant), with a plain-language `README - ORIGINALS.txt` in your interface language and a `MANIFEST.csv`
  (each original with its organized copy). **Restore originals** puts every file back and never overwrites;
  **Finish setting aside** settles a stop or crash. Duplicate albums copied once and albums split into tracks are
  handled.
- **Archive** (verified backup copy) and **Delete** (free the space) of parked originals, on the Originals card.
  *Archive…* copies the parked originals, with the README and manifest, to a folder you choose (not inside a
  collection folder), every file checked by SHA-256 and never overwriting; it can be stopped and continued, and the
  card says whether the archive is verified and still complete. *Delete…* is permanent and cannot be undone: it
  repeats every check (the organized copy's audio, and that each parked file is exactly the one set aside), lists
  what the organized copy does not carry (original tag values, lyrics), and needs the word **DELETE** typed, a tick
  that there is no backup when no verified archive exists, and a tick that you played the copies while the batch is
  younger than the cooling-off period (Settings → Retiring originals, default 7 days). Albums that fail a
  check are kept; a stopped or interrupted delete resumes; a record of the batch is kept in `~/.music-steward/deleted/`.
- The scanner ignores `_ORIGINALS`; a file restored with the same size and time keeps its saved hashes.
- **Required: the organized copy works in Subsonic-standard music servers** (Navidrome, and phone apps that speak
  the Subsonic protocol). A new test points a throwaway Navidrome at an organized copy and checks, through the
  Subsonic API, that every album appears once with all its tracks in order and the planned titles, artists, years
  and durations; CI runs it on macOS for every pull request. Tried by hand as well: four real albums split into
  tracks, served by Navidrome and played on a phone.
- **App icon**: the chosen concept, an ivory folder with a music note in negative space on teal, redrawn as a
  vector master (`assets/icon/*.svg`) by `tools/make_icons.py`, which also makes every size from the same shapes.
  Shown in the Dock for the desktop window, as the page's favicon and as the iPhone home-screen icon; a macOS
  `.icns` and an iOS 1024 px app icon are ready in `assets/icon/` for a packaged app later.
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

### Changed
- **`--root` adds a collection folder; it no longer hides the others.** `music-steward app` and `serve` show every
  collection folder the app knows, plus any named with `--root`; `serve` no longer requires `--root` once one is known.
- A plan made before its collection folder's path changed is no longer applied, resumed or retried (its source paths
  point at the old place, which may by then be another share): make a new plan.
- The header says *Collection folder* instead of *Root*.
- CI: GitHub Actions updated to actions/checkout v7 and actions/setup-python v7 (Node.js 20 is deprecated on runners).

### Fixed
- **A big collection no longer freezes the window.** On a library like the owner's (142,000 files, 18,000 albums,
  91,000 candidates) listing one folder's albums took 27 s and the whole collection's — and the Overview — 134 s;
  now 0.4 s and 1.5 s. The candidates are indexed by album and the list is read in a few queries instead of four per
  album (the result is identical). The folder tree now opens at once and the list follows with "Loading…"; a list
  that arrives after you have clicked elsewhere is dropped; long album lists show 500 rows at a time (*Show 500
  more*, and ↓ keeps going); while a job runs the window asks only for its progress, not the whole inventory.
- A stopped recognition said "Scan stopped"; it now says "Recognition stopped".
- Folder-wide bulk actions (*Accept all Ready + Likely*, *Add own info*, *Accept own info*, *Skip*) no longer decide
  for albums whose files are not found (or set aside): they are left as they were. Albums you tick are still acted on.
- An album deleted by *Recognize folder* (its folder now holds a different album) no longer passes its History lines to
  a later album that happens to get the same number.
- A scan of a folder that contains a collection folder no longer registers it as a second, overlapping collection
  folder; it is refused.
- In *Edit fields*, the greyed release values in the boxes (titles, artists) are no longer run through the interface
  translator: a title that happens to be an interface word is shown as it is.
- **Setting originals aside no longer loses your decisions.** After *Retire originals*, a rescan followed by
  *Recognize folder* used to drop the retired albums **and** your decisions for them. Now an album whose files are
  gone keeps its matches and its decision: it shows **Originals set aside** (after Retire) or **Not found** (the
  files are simply not there), and a new plan does not try to copy it again ("Albums whose originals are no longer in
  the collection folder: N"). Restoring the originals brings it back as normal.
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

[Unreleased]: https://github.com/luoxiaobin/music-steward/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/luoxiaobin/music-steward/releases/tag/v0.4.0
[0.3.0]: https://github.com/luoxiaobin/music-steward/releases/tag/v0.3.0
