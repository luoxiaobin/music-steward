# End-to-end trial — one thin set, the whole journey

The first slice (2026-11-19) took three albums through scan → recognize → review → plan → apply. Much has been built
since — Retire, Archive, collection folders, History, the Overview, the app — each tested on its own. This trial takes
**one small, hard set of about 20 albums through every step**, the way the owner will use it on the whole collection,
to find what is slow, confusing or missing before going wider. Terms are those of [GLOSSARY.md](../GLOSSARY.md).

Nothing here changes the collection folder until step 6, and step 6 only moves files into `_ORIGINALS` (and back).

## The set

The app picks it from its own database (it reads nothing on disk) and saves it on this Mac, not in the repository:

```bash
music-steward trial pick
```

It chooses albums nobody has decided yet, small ones first, so that together they cover:

| Kind | How many | What it tests |
|---|---|---|
| well known | 3 | a clean album with Latin-script tags: the happy path (*Ready*) |
| garbled | 3 | GBK/Big5 tags shown as `ÈÔÈ»…`: repair and Simplified names |
| compilation | 3 | a mainland compilation no catalogue knows: *No match*, then **Own info** |
| APE + cue | 2 | an APE image with a cue sheet: split into tracks, cue rewrite |
| FLAC + cue | 1 | a FLAC image with a cue sheet |
| multi-disc | 2 | CD 1 / CD 2 folders: one album folder, disc numbers |
| singles | 1 folder | a folder of single tracks: the singles rules |
| surround | 2 | 5.1 / DTS audio |
| duet | 2 | a track artist with two singers: each track's artist |

`music-steward trial show` lists them with where each one stands. In the app, the album list's **Show → Trial set**
shows just them. To pick again: `music-steward trial pick --replace`.

## The journey

Use the **app** for everything you would normally click, the command line where it says so. For each step note the
time it took and anything that surprised you (a line in the table at the end is enough).

**1. Recognize** — only the set:

```bash
music-steward recognize --trial
```

(about 10 s per album; Ctrl-C stops, the same command continues). Then in the app: **Albums → Show: Trial set**.

**2. Review**, album by album: accept a match, or **Own info**; fix what is wrong with **Edit fields** (album, year,
disc, each track's title and artist). Note every correction: they show where recognition needs work.

**3. Plan and copy** — only the set, to a local folder:

```bash
music-steward plan --trial --dest ~/Music/Trial
```

Then in the app: **Review plan →**, open that plan, check *Blocked* and *Left out*, **Apply**.

**4. Listen** — Navidrome on `~/Music/Trial` and Amperfy on the iPhone, as in [PHONE_TRIAL.md](PHONE_TRIAL.md), Parts
1–3, on these albums only.

**5. Look back** — **Overview** and **History**: do they tell the truth about what you did?

**6. Originals** — only if steps 1–4 went well: **Settings → Retiring originals → Allow** for the collection folder,
then on the plan's run **Retire originals…** (read the list of what will be set aside and why), and afterwards
**Restore originals** to see them come back. Archive and Delete are not part of this trial.

## Checklist

| # | Step | Time | ✅ / ⚠️ / ❌ | What you noticed |
|---|---|---|---|---|
| 1 | Recognize: how many Ready / Likely / Needs review / No match | | | |
| 2 | Review: albums accepted as matched / own info / skipped; corrections made | | | |
| 2a | Garbled tags: names right after repair? | | | |
| 2b | Compilations: is Own info good enough? | | | |
| 2c | Singles folder: sensible? | | | |
| 2d | Duets: each track's artist needed? | | | |
| 3 | Plan: blocked / left out items, and why | | | |
| 3a | Apply: time, anything not copied | | | |
| 3b | Folder names and file names in `~/Music/Trial` look right | | | |
| 3c | Split APE/FLAC images play as tracks | | | |
| 4 | Phone: albums once, order, Chinese text, covers, lyrics, multi-disc | | | |
| 5 | Overview and History agree with what you did | | | |
| 6 | Retire then Restore: understood what happened; files back | | | |

## What it decides

* Reviewing 20 albums in an evening, or a slog? → **bulk own info** and **singles rules** next, or not.
* The copies right on the phone? → the **Listen** screen (managed Navidrome, QR code), or Amperfy alone is enough.
* Trust in the copies to set originals aside? → Retire is the end of the journey, or a feature never used.
* Copies on the local disk fine, or wanted on the NAS? → **reliable NAS copying** next, or not.
