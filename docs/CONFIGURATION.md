# Configuration

Most choices are in **Settings** (the button in the header). They are stored in the app database and apply to the
next plan or recognition. An environment variable, when set, overrides the stored choice (useful for scripts); the
Settings panel then shows that choice greyed out.

## Naming defaults

These shape every organized copy. **Check them before your first plan.**

| Setting | Default | Example: before → after | Other choices | Variable |
|---|---|---|---|---|
| **Names in the organized copies** | **Simplified Chinese** | an artist spelled 鄧麗君 by one catalogue and 邓丽君 by another → one folder `邓丽君/` | Traditional Chinese; as each release has it (one artist may end up in two folders) | `MLT_SCRIPT=simplified\|traditional\|keep` |
| **Year in folder names and tags** | **Original release year** | a 2018 reissue of a 1986 album → `亲爱的小孩 (1986)` | year of the matched release | `MLT_YEAR=original\|release` |
| **Cue sheets** | **UTF-8** (with a byte-order mark) | a GB18030 cue that showed as `ÏéÇé…` in foobar2000 for Mac → readable Chinese | keep each cue's original encoding (for old Chinese Windows players) | `MLT_CUE_ENCODING=utf-8-sig\|original` |
| **Cue sheet for albums of separate track files** | **On** | `孟庭苇/真的还是假的 (1995)/` gets `真的还是假的.cue` listing its 10 files | off | `MLT_ALBUM_CUE=1\|0` |
| **Albums of one file with a cue sheet** | **Keep one file + cue sheet** | `蔡琴/老歌 (1990)/老歌.ape` + `老歌.cue` | **one file per track**: `01 - 恰似你的温柔.flac` … for music servers (Navidrome, Plex) and phone players that don't read cue sheets. Lossless: FLAC, cut at the cue sheet's exact positions, each track checked sample for sample against the image; the image in your collection is untouched. MP3 images stay one file (cutting would mean re-encoding) | `MLT_CUE_ALBUMS=image\|tracks` |
| **Retiring originals** | **Off**, per collection folder | with it on, after you confirm, the originals of albums whose copy was verified are moved (never deleted) into `_ORIGINALS` in that folder, with a README ([what it does](USER_GUIDE.md#11-your-originals-optional)) | — | — (a Settings choice only, never an environment variable) |
| **Default destination folder** | none | where plans copy to, unless you choose another folder | — | — |
| **Language** | **Automatic** (this computer's language) | — | English, 简体中文, 繁體中文, Français (Canada), Español (EE. UU.) | `MLT_LANGUAGE` |

Rules that always apply (not settings):

* **Layout:** `Album artist / Album (Year) / NN - Title.ext`; discs of a set: `1-01 - Title`, cue images
  `Album (CD 2).ape`; single tracks: `Artist / Singles / …`. Names are made safe for Windows and macOS.
* **Your edits win:** anything you type in Edit fields is used exactly as typed (not converted to Simplified).
* **One artist, one name:** when a catalogue gives an artist only in Latin letters ("George Lam") or as junk
  (`[unknown]`) while the album's own cue sheet or tags give a Chinese name (林子祥), the album's own name is used.
* **Duplicates:** albums that are byte-identical in your collection are copied once.

## Online catalogues

**Settings → Online catalogues**: MusicBrainz (always), Deezer (on by default, no account needed), Discogs (needs a
token, below). `MLT_SOURCES=musicbrainz,deezer,discogs` overrides.

## Credentials

Both are optional. Keys are read from the environment only; the app never stores them, never logs them, and never
puts them in URLs.

| | What it adds | How to get it | Without it |
|---|---|---|---|
| `ACOUSTID_API_KEY` | **Audio fingerprints**: the strongest evidence that a match is right; needed for the **Ready** status | Register an *application* (not a user key) at <https://acoustid.org/new-application>. Free for non-commercial use | Matching uses names and durations only; the album shows "Matched without fingerprints"; albums can still be **Likely** |
| `DISCOGS_TOKEN` | Discogs as an extra catalogue (good for Asian releases) | <https://www.discogs.com/settings/developers> → *Generate new token* | Discogs is not searched |

Fingerprints also need the **`fpcalc`** program from Chromaprint (`brew install chromaprint`). Without it,
fingerprinting fails quietly and matching continues without fingerprints.

Put the keys where your shell (and the app) will see them, for example in `~/.zshenv`:

```bash
export ACOUSTID_API_KEY=your-application-key
export DISCOGS_TOKEN=your-token
```

Open a new Terminal window afterwards. **Settings → Online catalogues** shows whether each key was found (never the
key itself).

## Where the app keeps its data

All in `~/.music-steward/` (override the database with `--db` or `MLT_DB`):

| Path | What | Safe to delete? |
|---|---|---|
| `library.sqlite` | the inventory, recognition results, your decisions, plans, settings, search index | No — this is your work |
| `journal/` | one journal per apply run; needed by **Resume** and **Undo** | Only together with the runs you no longer need to undo |
| `http-cache.sqlite` | answers from the online services (kept 30 days) and audio fingerprints | Yes (`music-steward cache --clear`); re-matching is then slower |
| `preview/` | MP3 previews made when you press ▶ (at most 1 GB, oldest removed first) | Yes |
| `webview/` | the desktop window's preferences (panel sizes, hidden columns) | Yes |

Back up `library.sqlite` and `journal/` **together**, with the app closed ([Recovery](RECOVERY.md#backing-up-the-apps-own-data)).

## What is sent to online services

Only while **recognizing** (or **Search again**). Nothing about your files' paths, your folders or your computer is
sent.

| Service | Receives | When |
|---|---|---|
| MusicBrainz | search terms: artist, album and track titles, track count and durations; release IDs to look up | every album |
| Deezer | search terms: album or track title and artist | every album, if enabled |
| Discogs | search terms, and your token (in a request header) | every album, if a token is set |
| AcoustID | audio fingerprints (computed locally from up to 2 minutes of a track) with their durations, and the application key | if a key is set |

Requests identify themselves with beets' standard client name. Answers are cached locally (above), so re-matching an
album sends nothing new for 30 days. Requests are throttled to each service's published rate limits.

## Other variables

| Variable | Effect |
|---|---|
| `MLT_DB` | app database path |
| `MLT_CACHE_DAYS` | how long web answers are cached (default 30) |
| `MLT_NO_CACHE=1` | turn the web cache off |
| `MLT_PREVIEW_CACHE_MB` | preview cache limit (default 1024) |
| `MLT_SCRIPT_RETRY=1` | also search in the other Chinese script (measured: no gain, 3× slower; off) |
