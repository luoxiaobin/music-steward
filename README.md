# Music Steward

[![CI](https://github.com/luoxiaobin/music-steward/actions/workflows/ci.yml/badge.svg)](https://github.com/luoxiaobin/music-steward/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

*Formerly MP3LibraryTool.*

**Music Steward organizes a large, messy music collection into a clean copy — without changing your originals.**
It reads your collection (for example on a NAS), recognizes albums (online catalogues, audio fingerprints, or the
album's own cue sheet, tags and folder names), lets you review every decision, and then writes an organized copy:
`Artist / Album (Year) / 01 - Title`, with corrected tags and a cue sheet per album.

It was built for collections where other tools struggle: **Chinese and other East Asian music** with garbled
GBK/Big5 tags, single-file "image" albums with cue sheets (APE, FLAC, WAV), and albums that no online catalogue knows.

**Status: 0.3.0, early release.** It works end to end and was tried on a real 3 TB collection, but it is installed
from source and needs Python and FFmpeg. See [`CHANGELOG.md`](CHANGELOG.md).

| Platform | Status |
|---|---|
| macOS (Apple silicon or Intel) | **Supported** — desktop window or browser; this is where it is used |
| Linux | Engine and tests run in CI; browser mode should work; the desktop window is untested |
| Windows | Not yet tested |
| Collection on a NAS (SMB) | Supported for **reading** (scan, recognize, preview) |
| Organized copy written to a **local or external disk** | Supported (the safest publish method) |
| Organized copy written **to the NAS** | Experimental — see [Recovery and safety](docs/RECOVERY.md#writing-the-organized-copy-to-a-nas) |

## Install (macOS)

**The app (no Terminal needed, Apple silicon):** download `Music Steward.app` from the latest release (or the
*macOS app* workflow's artifacts), and install FFmpeg and Chromaprint once with [Homebrew](https://brew.sh):
`brew install ffmpeg chromaprint`. Until builds are signed, open it the first time with right-click → Open. See
[docs/PACKAGING.md](docs/PACKAGING.md).

**From source:** you need [Homebrew](https://brew.sh). Then, in Terminal:

```bash
brew install python@3.12 ffmpeg chromaprint git
git clone https://github.com/luoxiaobin/music-steward.git
cd music-steward
python3.12 -m venv .venv
source .venv/bin/activate
```

Then choose **one** of the two ways to run it:

**A. Desktop window (recommended)**

```bash
pip install -e '.[desktop]'
music-steward app --root "/path/to/your/music"
```

**B. Browser tab** (also works from another computer through an SSH tunnel)

```bash
pip install -e .
music-steward serve --root "/path/to/your/music"
```

`serve` prints a one-time link (`http://127.0.0.1:…/?token=…`) and opens it in your browser.

Next time: `cd music-steward && source .venv/bin/activate`, then `music-steward app` (or `serve`): both remember
your collection folders, so `--root` is only needed to add one. If your music share is mounted under a new name, use
*Settings → Collection folders → Change path…*. `music-steward --version` shows the version.

**Recommended before recognizing:** an AcoustID key for audio fingerprints (free for non-commercial use) and optionally a
Discogs token. Without them recognition still works, by names and durations only. See
[Configuration — credentials](docs/CONFIGURATION.md#credentials).

## Your first run, in short

1. Point it at a **small folder** first (a few albums), not your whole collection.
2. **Scan**, then **Recognize folder** (about 10 seconds per album).
3. In **Albums**, review each album: accept a match, use the album's **own info** when no catalogue knows it, or skip.
4. **Settings → Default destination folder**: an empty folder **outside** your collection.
5. **Review plan → Build plan**: check the folder-tree preview, then **Apply**.
6. Open the copies in your player. Not happy? **Undo run** removes them.

The full walkthrough, including an album no catalogue knows, is in the [User guide](docs/USER_GUIDE.md).

## What it reads and writes

| | Reads | Writes |
|---|---|---|
| Your collection (the `--root` folders) | file listings, audio and tags | **nothing** — unless you switch on *Retire originals* for a folder (off by default): then, after you confirm, the files of albums whose copy was verified are **moved** (never deleted) into a folder called `_ORIGINALS` in the same collection folder, with a README, and can be restored |
| The destination folder you choose | whether a name is taken | the organized copies (only on **Apply**); **Undo** deletes exactly the copies a run created, if unchanged |
| App data `~/.music-steward/` | — | the app database, apply journals, web lookup cache, preview cache, window preferences ([details](docs/CONFIGURATION.md#where-the-app-keeps-its-data)) |
| Internet | — | search terms (artist, album, track names, durations) to MusicBrainz, Deezer, Discogs; audio fingerprints to AcoustID ([what is sent](docs/CONFIGURATION.md#what-is-sent-to-online-services)) |

Scanning, recognizing, reviewing and planning change no music files. **Apply** writes only to the destination.
The one thing that ever touches your collection is the optional, confirmed *Retire originals* step (a move, restorable;
see the [User guide](docs/USER_GUIDE.md#11-your-originals-optional)). What can still go wrong, and how to recover, is in
[Recovery and safety](docs/RECOVERY.md).

## Documentation

| Read this | For |
|---|---|
| [User guide](docs/USER_GUIDE.md) | scan, recognize, review, own info, editing, search, plan, apply, undo — step by step |
| [Recovery and safety](docs/RECOVERY.md) | stopping, resuming, undo, conflicts, a disconnected drive, backups, what is guaranteed and what is not |
| [Configuration](docs/CONFIGURATION.md) | naming defaults with examples, settings, credentials, data locations, privacy |
| [Architecture](docs/ARCHITECTURE.md) | components, processes, safety boundaries, tests |
| [Feature sheet](docs/FEATURE_SHEET.md), [Plan](docs/PLAN.md), [Evidence report](m0/EVIDENCE_REPORT.md) | product overview, roadmap and decisions, measured experiments |
| [Glossary](GLOSSARY.md) | the words used in the product, its screens and its documents |
| [AGENTS.md](AGENTS.md), [SECURITY.md](SECURITY.md) | contributing (people and coding agents), reporting a security problem |

## Command line

Everything the app does is also available as commands (`music-steward <command> --help` for details):

```bash
music-steward scan "/Volumes/music/音乐Ape"        # inventory a folder (reads only); re-run to pick up changes
music-steward hash "/Volumes/music/音乐Ape"        # audio hashes for duplicate detection (reads only)
music-steward audit                                # garbled tags, unreadable files, duplicates, cue sheets …
music-steward recognize "/Volumes/music/音乐Ape"   # match the albums in a folder (resumable)
music-steward plan "/Volumes/music/音乐Ape" --dest ~/Music/Organized   # build a plan (writes nothing)
music-steward apply <plan>                         # copy, tag the copies, verify, publish
music-steward resume <run>                         # finish an interrupted run
music-steward undo <run>                           # remove the copies a run created (unchanged ones only)
music-steward cache                                # web lookup cache size (--clear to empty it)
music-steward inspect "song.mp3" --hash            # one file, no database
```

The app database defaults to `~/.music-steward/library.sqlite` (override with `--db` or `MLT_DB`).

## Tests

```bash
pip install -e '.[dev]'
python -m pytest
MLT_APE_SAMPLE=/path/to/any.ape python -m pytest   # also run the optional APE tests
```

Test audio is generated with FFmpeg. **Never commit real music.** CI runs the suite on Linux and macOS for every pull
request.

## License

GPL-3.0-or-later (see [`LICENSE`](LICENSE)). It uses beets (MIT), mutagen (GPL-2.0-or-later), pywebview (BSD),
OpenCC (Apache-2.0) and calls FFmpeg and Chromaprint as external programs.
