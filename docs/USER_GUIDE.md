# User guide

This guide takes you from a small folder of albums to a verified organized copy. It assumes Music Steward is
installed ([README — Install](../README.md#install-macos)). Nothing in steps 1–6 changes any music file; **Apply**
(step 8) writes only to the destination you choose. Only the optional step 11 (off unless you switch it on) ever moves
files in your collection, and it never deletes.

**Start small.** Copy three or four albums into a test folder — ideally one well-known album, one disc of a set, and
one album no online catalogue will know (a cue-sheet image with Chinese text is a good test). Point the app at that
folder first.

## 1. Open the app

```bash
cd music-steward && source .venv/bin/activate
music-steward app --root "/Users/you/Music/test-albums"
```

The window has three panels: **folders** on the left, a **list** in the middle (files, or albums), and **details** on
the right. Drag the gaps between panels to resize them; double-click a gap to auto-size. **Columns** hides list
columns you don't need. **Settings → Language** switches the interface (English, 简体中文, 繁體中文, Français,
Español).

## 2. Scan

Click **Scan**. The app lists every file, reads its audio properties and tags, and finds problems:

* **Text decoding issues** — tags stored as GBK/Big5 bytes, shown as `ÈÔÈ»…`. The app proposes the real text (for
  example 林子祥) but does not write it to your files; it is used for searching and for the organized copies.
* **Unreadable**, **Wrong extension** (an `.ape` file that is really MP2), **Exact duplicates**, **Same audio,
  different tags**, **Cue sheets**, **Lyrics without a track** — under **Filters** on the left.

Re-scanning later only reads new or changed files. **Re-scan folder** re-reads just the folder you are in.

## 3. Recognize

Select the folder and click **Recognize folder** (it asks first; a big folder takes about 10 seconds per album, and
**Stop** in the status line stops after the current album). Each album is matched against MusicBrainz, Deezer and
Discogs, using its tags, track lengths and — if you set up an AcoustID key — audio fingerprints.

Switch to **Albums**. Each album gets a status:

| Status | Meaning | What to do |
|---|---|---|
| **Ready** | Fingerprints confirm the match | Accept (bulk is fine) |
| **Likely** | Very close match, every track mapped, nothing missing | Accept after a spot check (bulk is fine) |
| **Needs review** | A plausible candidate, but something differs | Look at it |
| **Weak: title agrees** | Poor match, but the album title agrees | Usually use own info |
| **No real match** | No catalogue knows this album | Use own info |

A red **title differs** next to a candidate means the match names a *different album* — often another album by the
same artist. Don't accept those in bulk.

## 4. Review an album

Click an album (or use **↓/↑**). The details panel shows:

* **Candidate releases** with the evidence: distance (lower is closer), how many fingerprints agree, missing tracks,
  "your files are disc 2 of 3".
* **Track mapping** — which of your files maps to which release track. Press **▶** to listen to any track; APE and
  single tracks inside a cue image are converted to a preview when you press play.
* The action bar at the top: **Accept** (A), **Own info** (O), **Skip** (S), **Undo** (U). After a decision the next
  album opens.

If the right release isn't listed: **Search again** with your own artist and album words, or paste a MusicBrainz
release link.

### An album no catalogue knows ("own info")

Many albums — especially Chinese releases, compilations and audiophile test discs — exist in no online catalogue.
Click **Own info** (O). The app builds the release from what the album itself says:

* a **cue sheet**: album title, performer, year, every track title;
* else the **tags** (garbled ones repaired), then **file names** (`03 - Title.ape`), then the **folder name**
  (`Artist - Album (1998)`);
* placeholders such as 未知标题 / "Unknown Artist" are ignored, download-site noise (`.-.`, `.专辑`, `(APE)`) and
  genre prefixes (`纯音乐-钢琴-…`) are removed.

The candidate list shows where each field came from ("album from cue sheet · year from folder name").

### Fix names: Edit fields

After accepting, open **Edit fields** to correct the album, album artist, year, disc number ("Disc 2 of 3"), any
track title or any track's **artist** (a duet, a guest singer, a compilation of several singers). What you type is used
exactly as typed, also for the folder name. A track's artist goes into that track's tags and the album's cue sheet;
the album folder follows the album artist, so the album stays together.

**Discs of one set** (separate folders `… CD1`, `… CD2`): give them the same album, album artist and year and each
its own disc number; they go into one album folder.

## 5. Review many albums quickly

* **Accept all Ready + Likely** accepts the top match of those albums in the folder.
* **Show** filters the list by status; **only where the album title agrees** narrows it further. Tick albums (or the
  header box for all shown) and use **Accept top match**, **Use own info**, **Skip** or **Clear decision**. If any
  ticked album's match has a different title, the app offers to accept only the ones that agree.
* **Add own info to No match + Weak**, scan the "Own info:" lines in the list, then **Accept own info**.
* **Filter this list…** narrows the current folder as you type.

Every decision is saved at once and can be undone; albums left undecided are simply left out of the plan.

**Renaming or moving folders in your collection is fine**: on the next scan the app recognises the files by their
audio, and their albums keep your decisions. If an album's **audio** changes after you decided (a file replaced by
another recording), it is marked **Audio changed · decide again** and left out of plans until you decide again.
Changing only tags does not count.

## 6. Find anything: search

The search box at the top (or **/**, **⌘K**) finds albums, folders and tracks across the whole collection: any part
of a name, Simplified or Traditional (邓丽君 finds 鄧麗君), pinyin (`denglijun`, `deng li jun`, initials `dlj`), and
repaired tag text. Click a result (or ↑/↓ and Enter) to jump there.

## 7. Plan

Open **Settings** once: set the **Default destination folder** (use **Choose…**, or type the path — a line under the
field tells you at once if it is written correctly, whether the folder exists and whether it can be used; if you
paste a network address such as `//nas/music/…`, it offers the path as your Mac shows the connected share, under
`/Volumes`). This is where the organized copies go; it is not the folder to scan. If you play your music through a
music server or a phone app that doesn't read cue sheets, set **Albums of one file with a cue sheet** to **One file
per track**. It must be an empty folder outside
your collection — on a local or external disk for now. Check the naming defaults there too
([what they do](CONFIGURATION.md#naming-defaults)): names in Simplified Chinese, the original release year, a cue sheet
for every album.

Click **Review plan → Build plan**. Building a plan writes nothing. The plan screen shows:

* **Result preview** — the destination as a folder tree (Artist / Album / files);
* **Done for you** — what your naming rules changed (artist names unified, Simplified, original years, duplicate
  albums copied once, album cue sheets written);
* **Blocked** — items that will be skipped, with the reason (for example two different albums that would get the
  same name — fix one of them in review and build the plan again);
* tag changes and path changes, file by file.

## 8. Apply

Click **Apply · copy N files**. Each file is copied, tagged (the copy only), verified (the audio bytes must be
identical to the original) and only then given its final name. A progress bar shows files, size, speed and time
left. **Stop** halts after the current file; **Resume** later continues. Files that *failed* (for example the drive
disconnected) are listed under **Not copied**: fix the cause and press **Retry failed**. See
[Recovery and safety](RECOVERY.md#resume-or-retry-failed).

## 9. Check the result

Open the destination in Finder and play some albums in your player. Cue-sheet albums kept as one file need a
cue-aware player (foobar2000, VOX, Swinsian…): open the `.cue` file; albums split into tracks play anywhere. Every album has a cue sheet, also albums of separate track files.

## 10. Undo, or keep going

Not what you wanted? On the plan screen, **Undo run** removes exactly the copies that run created (copies you have
changed since are kept). Adjust your decisions or settings, build a new plan, apply again.

When the test folder looks right, repeat with a bigger folder. On a big collection, work folder by folder.

## 11. Your originals (optional)

A copy of a big collection means two collections. Once you have played the organized copies and are happy, Music
Steward can **set the originals aside** so you end up with one organized library. It is off until you allow it:
**Settings → Retiring originals → Allow for <your collection folder>**.

**Retire originals.** On the plan screen, next to the run, press **Retire originals…**. The app re-checks every album
first and lists what can be set aside and what stays (with the reason): an album is set aside only if all its files
were copied, the organized copy is still there with *identical audio* (retagging it is fine), and the original has
not changed since. You tick a box that says the organized copy becomes your only finished copy — *keep your own
backup* — and confirm. The originals are then **moved, never deleted**, into `_ORIGINALS/run…/` inside the same
collection folder, on the same drive: it takes seconds, whatever the size. Anything not part of a verified album
stays exactly where it is.

**What the `_ORIGINALS` folder is.** Nothing else in a collection looks like it. Each batch has a
`README - ORIGINALS.txt` (in your interface language) saying what it is and what you can do, and a `MANIFEST.csv`
listing every file with its original path and its organized copy, so you can restore by hand without the app.

**What you can do with them:**

| | |
|---|---|
| **Restore** | On the plan screen, under **Originals set aside**, press **Restore originals**: every file goes back to where it was. Anything that has appeared at an original place since is left alone and reported; nothing is overwritten. |
| **Archive** | Press **Archive…** and choose a folder on a backup drive or share (never inside your collection). Every parked file is copied with its README and manifest, checked by SHA-256 and never overwritten. You can stop and continue. The card then says *verified* and keeps checking that the archive is still all there. The parked originals stay. |
| **Delete** | Press **Delete…** to free the space. **It cannot be undone.** The app repeats every check first (the organized copy's audio is intact; each parked file is exactly the one set aside), shows what the organized copy does *not* carry (original tag values, lyrics), and asks you to type **DELETE**. If there is no verified archive you must also tick that you have no backup; and if the originals were set aside less than the cooling-off period ago (7 days by default, Settings), you must tick that you have played the copies. Albums that fail a check are kept. |

Interrupted (the app quit, the drive dropped)? **Finish setting aside** on the same card settles what was
half-done: every file is either at its old place or in `_ORIGINALS`, never lost.

## 12. When your collection folder moves

Music Steward remembers every collection folder; starting it with `--root` adds one, it never hides the others.

**Not connected.** If the drive or share is not mounted, the folder panel says *This collection folder is not
connected*. Nothing is lost, and nothing that reads the folder can start. Connect it (Finder → Go → Connect to Server)
and press **Check again**. If the same share is already mounted under another name (macOS sometimes mounts
`/Volumes/music` as `/Volumes/music-1`), the notice shows that path and offers **Change path…**.

**Change path…** (also in **Settings → Collection folders**) is for *the same collection at a new path*. Choose the
new folder; the app compares up to 200 files it knows with the new folder (same place, same size — nothing is read
or written there) and accepts it only if at least 90% match. Your albums, decisions and edits stay; the next scan
reads only what differs. Afterwards *Retire originals* is off again for that folder, and plans made before the change
cannot be applied any more: make a new plan. The change waits while a job is running, while a copy is unfinished
(finish it with Resume or undo it), and while originals are still set aside in `_ORIGINALS` (restore or delete them
first). From the command line: `music-steward collection list` and `music-steward collection set-path OLD NEW`.
