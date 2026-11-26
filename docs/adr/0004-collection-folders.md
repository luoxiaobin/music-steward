# ADR 0004: Collection folders — changing, adding and hiding them

* **Status:** Accepted (2026-11-24). Every decision below was settled with the owner in a design interview
  (2026-11-21) and approved on 2026-11-24. **Built:** all rules — 5, 6 and 8–12 (*Change path*, *not connected*,
  `--root` adds, share identity) on 2026-11-24 (#18); 3, 4, 13 and 14 (*Add*, *Hide*, the folder switcher) on
  2026-11-26; rule 7 with the lifecycle fixes (#16).
* **Date:** 2026-11-21
* **Deciders:** Kevin Luo

Terms are those of [GLOSSARY.md](../../GLOSSARY.md).

## Context

An owner typed their NAS address into *Default destination folder* and could not rescan. The app accepted it
silently, and there was no way to see, change or add the **collection folders** from the app at all.

What the code does today:

* the app takes its list of collection folders at start-up and cannot change it while running; `--root` means
  "show only these", so starting with a new one hides the old collection;
* the screen works on one collection folder at a time;
* files, albums, decisions, plans, scans and parked originals all belong to one collection folder, and a file's
  identity is tracked per folder (moving an album *between* folders loses its decision);
* plans, apply journals and batches of parked originals store full file paths, and a journal is append-only;
* a collection folder only gets its database record on its first scan.

The change people really need is rarely "add a folder". It is **the same collection at a new path**: the share
is mounted under another name (`/Volumes/music` → `/Volumes/music-1`) or the NAS address changed.

## Decision

### Build order

1. **Change path** of an existing collection folder, with the **not-connected** message that usually triggers it,
   comes first.
2. **Adding** a collection folder and **hiding** one follow later as small separate steps, by the rules below.

### Rules that hold for every scenario

3. **No overlap.** Collection folders may not contain or sit inside one another. A folder already inside a collection
   folder is not added: the app says so and opens it in the tree (`Ape.Flac` needs no folder of its own). A folder
   that contains a collection folder is refused. A hidden collection folder still counts.
4. **Adding only registers.** It reads nothing, writes nothing and does not scan; the app offers "Scan now" because a
   big share takes hours. A new collection folder starts with *Retire originals* off.
5. **`--root` adds.** On the command line `--root` adds folders to the ones the app already knows instead of
   replacing them, so it can never hide a collection again.
6. **Not connected.** A collection folder whose drive or share is not mounted is listed as *not connected*; the app
   still opens, scan / copy / retire / archive / delete are disabled for it, and a *Check again* button is offered.
   The app never removes it by itself.
7. **Albums that are not found are never dropped.** An album whose files are not at the current location raises the
   *not found* exception (a warning, not a state) and keeps its decision, matches and edits; plans leave it out; it comes back by itself if its files
   return; it is discarded only if the owner says so. (Today "Recognize folder" drops the album *and* its decision.)

### Changing the path

8. **Same-collection check.** The app samples up to 200 known files and checks each exists at the same relative
   path under the new folder with the same size. At least **90%** must match, otherwise the change is **refused**
   ("this does not look like the same collection; add it instead"). There is no override. A path that is already
   registered as another collection folder is refused too; merging two is out of scope.
9. **Blocked while** a scan, copy, retire, archive or delete is running; a copy run was stopped or interrupted and is
   unfinished; or originals are still parked in `_ORIGINALS` (restore or delete them first). The message says which.
   Finished runs do not block: undoing one touches only the destination.
10. **After the change:** no automatic scan ("Scan now?" is offered); albums, decisions and plans stay (the next scan
    re-reads files whose size or time differs and recognises them by their audio); *Retire originals* is switched back
    **off** for that folder; a history entry records old path, new path, time and the sample result.
11. **Where.** *Settings → Collection folders* lists each folder with its path and status and a *Change path…* button;
    the not-connected message offers the same button. Flow: pick the new path (Choose… and the live folder check) →
    same-collection check → one confirmation → done. Disabled while any job runs. A command-line twin,
    `music-steward collection set-path OLD NEW`, runs the same checks. There is no separate undo: change it back.
12. **Share identity.** When a network collection folder is scanned, the host and share name are stored locally
    (never a user name or password; never in the repository). If the folder is not connected and the same share is
    mounted elsewhere, the message offers "Change path to it".

### Hiding

13. **Hide** means "stop showing and scanning". It keeps everything — inventory, albums, decisions, plans, history of
    parked originals — and showing it again, or adding the same path, brings it all back. The app has **no**
    "forget everything" action.

### Screen

14. One collection folder at a time, as now. A switcher is built together with *adding*.

## Considered and rejected

* **Rewriting the stored paths on a path change.** Journals are append-only; rewriting them risks Resume, Undo and
  Restore. Blocking the change while a path is still needed is safer and rarely in the way.
* **Nested collection folders.** Files and decisions would be counted twice.
* **Overriding the 90% check.** It would let another collection inherit this one's decisions.
* **Scanning automatically after adding or changing.** Hours of reading a NAS, started by surprise.
* **A "forget everything" button.** The only way to lose the owner's review work in one click.
* **`--root` replacing the list** (today). It is what hides a collection after a typo.

## Draft wording (for approval; fixed sentences, translated; paths shown separately as data)

* Not connected: "This collection folder is not connected. If the share is mounted somewhere else now, choose *Change
  path…*. Otherwise connect it (Finder → Go → Connect to Server) and press *Check again*."
* Suggestion: "The same share is now at *{path}*. Change path to it?"
* Accepted: "Checked 200 files: 198 are at the new path with the same size. Your albums and decisions stay. The next scan
  updates the inventory."
* Refused: "Only 63 of 200 files were found at that path, so this does not look like the same collection. To use it as
  another collection folder, add it instead."
* Already registered: "That folder is already a collection folder of its own, with its own albums and decisions."
* Blocked: "A scan is running." / "A copy is unfinished (run #4). Finish it or undo it first." / "Originals are still set
  aside in _ORIGINALS. Restore or delete them first."
* After: "Path changed. Retire originals is off for this folder again. Scan now?"
* Not found: "Albums not found at this location: N. Their decisions are kept."

## Consequences

* The album lifecycle changes (rule 7): units whose files are gone stop being deleted on "Recognize folder"; the
  list, the plan and the search must understand *not found*.
* Database: a collection folder gains *hidden*, *share host* and *share name*; a history table records path changes.
* The screens still say "Root", "Source root", "Destination root" and "No root configured"; they become
  *collection folder* / *destination folder* with their translations, as the glossary says.
* `--root` changes meaning (rule 5): noted in the CHANGELOG.
* Work items, in order: finish *Folder check*, *Archive and Delete*; then the not-connected message and *Change path*
  with rules 6–12; later *Add* and *Hide*.

## As built (2026-11-24)

* `music_steward/collection.py` holds the checks; the app (*Settings → Collection folders*, the not-connected notice
  above the folder tree) and `music-steward collection list | set-path OLD NEW` both use it.
* The only stored path that changes is the collection folder's own (`root.path`). Plans hold full source paths, so a
  plan made before the change is refused at Apply, Resume and Retry (`apply/runner.py: sources_moved`) — the old path
  may by then be another share. Failed files of an old run are therefore not a blocker: *Retry failed* refuses them
  afterwards, and a new plan copies them.
* *Retire originals* still works for runs copied before the change: it verifies the organized copy against the audio
  fingerprint recorded at copy time and moves the original found at the *current* path, after checking its identity.
* A job that reads a collection folder (scan, recognize, copy, retire, restore, archive, delete) is refused while it
  is not connected; *Undo run* is not, since it only touches the destination.
* "A job is running" is known to the app that runs it; the command line cannot see a job running in an open app
  window, so change the path in the app while it is open.

## As built: adding and hiding (2026-11-26)

* *Settings → Collection folders* has **Add…** and, per folder, **Hide** / **Show**; the command line has
  `music-steward collection add | hide | show PATH`. When more than one collection folder is shown, the header holds a
  switcher; the screen still shows one collection folder at a time.
* **Add** checks the folder (exists, is a folder on this Mac), refuses a folder that is or contains a collection
  folder (hidden ones included) and one that is or overlaps the default destination folder, and for a folder inside a
  collection folder offers **Open it** there instead. It inserts the folder's record only: no scan, no file read.
  Adding the path of a hidden folder shows it again.
* **Hide** is refused while a job runs and for the last folder shown (the app would have nothing to show). A hidden
  folder is refused by every job that reads it, including a scan typed on the command line; *Undo run* still works.
  `--root` with a hidden folder's path shows it again (rule 5).
* A scan can no longer create a collection folder that contains another one (it used to register one silently).
