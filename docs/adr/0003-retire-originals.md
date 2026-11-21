# ADR 0003: Retire the originals — a guided, verified, reversible step

* **Status:** Proposed (2026-11-21), revised the same day after the owner's direction: the parked originals are
  clearly labelled, and **Archive** and **Delete** are offered in the app, each behind strong warnings and
  confirmations. Needs the owner's answers to the **open decisions** before any code is written.
* **Date:** 2026-11-21
* **Deciders:** Kevin Luo

## Context

Music Steward organizes a **copy** of the collection and never touches the originals. For a 3 TB collection that
means a second 3 TB somewhere, and — once the copy is good — a collection on the NAS that is now duplicated and
unorganized. Users with a big collection need a safe way to *finish*: end up with one organized library, not two.

The first rule of the product is "sources are read-only" (AGENTS.md P0). This feature is the **only** place that
rule can bend, so it has to be designed as an exception with hard edges, not as a delete button.

## Goals and non-goals

* Goal: after an album has been copied **and verified**, let the user take its originals out of the way, album by
  album, in a way they can **undo**.
* Goal: nothing is ever lost because of a bug, a crash or a misclick: every step is checked again at the moment
  it happens, journalled first, and reversible.
* Goal: let the user **choose what happens to the originals** — keep them parked, **archive** them (a verified
  backup copy elsewhere), or **delete** them to save space — each an explicit, separate, confirmed action. The
  app never decides to delete on its own.
* Goal: the parked originals are **unmistakable**: anyone opening the folder sees at once what it is, where the
  organized copies are, and what they can do.
* Non-goal: acting on anything that was skipped, undecided, blocked, flagged "Audio changed", or not part of a
  verified run.

## Decision (proposed)

**Retiring is a move.** A retired original is moved, with its relative path, into a holding folder on the
**same volume**: `<root>/_ORIGINALS/<run id>/<original relative path>`. A rename on the same share is instant,
atomic and keeps every byte; nothing is read or rewritten. What happens to it afterwards is the user's choice
(see *Your originals*): **Restore**, **Archive**, or **Delete**.

### What may be retired (all must hold, checked again when the user confirms)

| Check | Why | How |
|---|---|---|
| The file belongs to an album whose **whole plan item set is committed** in a run that is **not undone** | no half-organized album loses its originals | the run's journal: every item of the unit `committed` (own copy) or `already_existed` (an identical file was there) |
| The **organized copy still exists** and its **audio is unchanged**: audio fingerprint equals the journal's `src_audio` | proves the music itself is safe at the destination, also if the user retagged the copy since | `hashing.copy_audio_fingerprint` on the destination (a local disk: fast) |
| The **source is unchanged** since it was copied: size, modification time and content identity (ADR 0002) equal what the run recorded | never retire a file that is not the one that was verified | `os.stat` + `identity.audio_identity` |
| Split albums (cue image → tracks): **every track** of the image verified, then the image and its cue sheet may go | the image is only redundant once all its tracks are safe | all `split` items of that image `committed` |
| Side files (cue, lyrics, covers) of the album that were copied | small; a copy changed by the user is **flagged**, not blocking (the original stays reachable in the retired folder) | sha of the copy vs. the journal |
| Everything stays **inside the root**, on the **same device**, and no path or folder on the way is a symlink | the exception must not become a way out of the collection | `realpath` checks like apply's `_guard`; `st_dev` equal; refuse when `_ORIGINALS` is a link |

Anything else in a source folder — files that were never part of the plan, other albums, notes — **stays where it
is**. Only the files of verified albums move, one by one, mirroring their paths. A folder is never moved as a
whole.

### Process

1. **Dry run first, always**: *Retire originals…* lists, per album, "can retire" or "can't, and why" (first failing
   check), with counts and sizes. Nothing changes.
2. **One explicit confirmation** that names what happens: "Move 118 albums (412 GB) into `/…/_ORIGINALS/7/` on
   the same share. Nothing is deleted. You can restore them." Plus the redundancy acknowledgement (below).
3. **Journal-first moves**: per file, an intent record (source, target, identity), then `rename`, then a
   `moved` record — the same discipline as apply. A crash leaves every file either at its old path or at the
   new one, never in between; **Resume** finishes the batch; each file's checks are repeated before its move.
4. **Restore** (per album, or the whole batch): moves back what is still there, refuses to overwrite anything
   that has appeared at the original path since (reports it), and tells which files are no longer in the retired
   folder.
5. **Labelling** (below): every batch gets a `README` and a manifest. Retired files are recorded in the app database (a `retired` table keyed by the file's identity) so the UI shows
   "Retired", and a later scan does not treat them as new or missing. The scanner **ignores `_ORIGINALS`**
   (like junk folders), so the moved files are not rediscovered as albums.

### Opt-in and the rule in AGENTS.md

* **Off by default, per root.** Settings → *Allow retiring originals in this collection folder* (a checkbox per
  root, with a short explanation). Without it the code path is unreachable and "sources are read-only" holds
  exactly as today.
* AGENTS.md P0 changes from "any code path that could write, rename … under a configured root" to: **only
  `retire` may rename files under a configured root, only into `<root>/_ORIGINALS/`, only for files that
  passed the checks above; only the confirmed **Delete** action may delete, and only files inside
  `_ORIGINALS/<run>/` after its checks; any other write, rename, truncation or deletion under a root is P0.**
  Codex and reviewers then enforce the narrower rule.

### The redundancy warning (the one risk this feature adds)

Today there are two copies. After retiring there is **one organized copy** (the originals are only parked).
Deleting the parked originals later makes the organized copy the *only* copy, on whatever disk the destination
is — often a single external drive. The confirmation therefore says it plainly and asks for a tick: *"I know the
organized copy will be the only copy once I delete the retired originals, and I have (or will make) a backup."*
The app does not make backups; it will say so.

### Your originals: labelled, with three choices

**The label.** The holding folder is named `_ORIGINALS` — nothing else in a collection looks like it — and each
batch folder `_ORIGINALS/<run id>/` holds:

* `README - ORIGINALS.txt`, in the interface language, plain words: *"These are the ORIGINAL files that Music
  Steward replaced with organized copies on <date>. Nothing in here has been changed. The organized copies are in
  <destination>. You can: (1) **restore** them from the app (Originals screen), or by moving the files back to
  the paths in `MANIFEST.csv`; (2) **archive** them: copy this folder to a backup drive and keep it; (3) **delete**
  them to save space — only after you have checked the organized copies in your player and have a backup of them.
  Deleting cannot be undone."*
* `MANIFEST.csv`: for every file its original path, size, content identity, the path of its organized copy, and
  the album — enough to restore or audit by hand, without the app.

**The Originals screen** (one row per batch: date, albums, files, size, state) offers, per batch or per album:

| Action | What it does | Warning / confirmation |
|---|---|---|
| **Restore** | moves the files back to their original paths (refuses to overwrite anything that has appeared there; reports it) | one confirmation; always safe |
| **Archive…** | copies the batch to a folder the user picks (an external drive, another share) with the same journalled, no-overwrite, **sha-256 verified** protocol as apply; the parked originals stay until deleted. The batch then shows **"Archived and verified"** | one confirmation naming the target and the size; the target may not be inside a collection folder |
| **Delete…** | permanently deletes the parked originals to free space | the strongest warning in the app, below |

**Delete is a separate, deliberate act.**

* It is offered only for files inside `_ORIGINALS/<run id>/`, and only after the checks that allowed retiring are
  **repeated just now** for each album: its organized copy exists and its audio fingerprint still equals the
  journal's. An album whose copy no longer verifies is skipped and reported — never deleted.
* The dialog states, in plain words and before anything can be typed:
  * how many albums / files and **how much space it frees**;
  * **"This cannot be undone. There is no Trash."**;
  * the state of the backup, in colour: **green** — *"A verified archive of these originals exists at …"*; **red** —
    *"No archive. After this, the organized copy is your only copy. Music Steward does not make backups."*;
  * what is **not carried over** to the copies and will be lost for good: the originals' tags (including garbled
    text the app repaired), the original cue sheets of albums split into tracks, files of the album that were never
    copied (listed);
  * **how long ago** the batch was retired. If it was under 7 days ago (default; the user may change it in
    Settings), an extra tick: *"I have played the organized copies and checked them."*
* Confirmation takes **typing the word DELETE** (not a button click) together with a second tick for the red
  no-archive state: *"I understand I have no backup of the organized copies."*
* It runs **journal-first**, per file: an intent record, then the delete of one file whose size, modification time
  and content identity still equal the manifest (a file that changed is left alone), then a record. Stop and
  Resume work; empty folders in the batch are removed afterwards. It never follows a link, never leaves
  `_ORIGINALS/<run id>/`, and the manifest stays in the app's own data (`~/.music-steward/`) as the record of what
  was deleted.

**Instructions for the user** are given in three places, with the same words: the README in each batch folder,
a *Your originals* section in the User guide, and the dialogs above.

## Alternatives considered

| Option | Why not |
|---|---|
| Delete the originals automatically after verifying | irreversible with no second look; a verification bug becomes data loss. Deletion exists only as a separate, confirmed action on originals that are already parked and labelled. |
| macOS Trash | SMB shares have no Trash the app can rely on; emptying the Trash loses the path needed to restore; the Synology `#recycle` folder is ignored by the scanner and not under our control. |
| Move whole folders | would take along albums that were not copied or verified. Rejected: per-file moves of verified albums only. |
| A "move" mode in apply (copy, verify, delete) | removes the review between *copy* and *originals gone*; one action, no second look. Rejected. |
| Retire to another volume | a copy across volumes is a second 3 TB transfer, and a crash leaves two half-states. Same-volume rename only. |

## Failure modes to test (failure injection, like apply)

* crash between the intent and the rename, between the rename and the `moved` record, mid-batch; Resume and
  Restore each leave every file in exactly one place;
* a source changed after the dry run (size, mtime, content) is skipped, not moved;
* the copy's audio changed or the copy deleted after the dry run: its album is skipped;
* a symlink planted at `_ORIGINALS`, at a parent folder, or at a source path: refused, nothing moved;
* the target path already exists: nothing overwritten (`rename` without replace);
* the run was undone after the dry run: its albums are skipped;
* restore when the original path is occupied: reported, nothing overwritten;
* never retires: skipped / undecided / blocked / "Audio changed" albums, albums with a failed or missing item,
  files outside the verified albums;
* a root without the opt-in: every entry point refuses;
* **archive**: crash mid-copy resumes; a file that fails verification is reported and the batch is not marked
  "Archived and verified"; the target inside a collection folder or a symlinked path is refused; nothing
  overwritten at the target;
* **delete**: refused without the typed word and the ticks; a file changed since it was parked is left alone; an
  album whose copy no longer verifies is skipped; a path outside `_ORIGINALS/<run>/`, a link, or a folder that
  became a link is refused; crash mid-delete resumes without deleting anything twice or anything else;
  the "not carried over" list is complete (uncopied files, split-album cue sheets, repaired tags);
* the README and MANIFEST are written before the first file moves, and list every file of the batch.

## Open decisions for the owner

| # | Question | Recommendation |
|---|---|---|
| 1 | Opt-in per collection folder, off by default? | **Yes** |
| 2 | Require the redundancy tick (organized copy becomes the only copy once the parked originals are deleted)? | **Yes** |
| 3 | Where do retired originals go, and what is the folder called? | **`<root>/_ORIGINALS/<run>/…`**, same volume, instant, reversible; with a README and a manifest in every batch |
| 4 | Archive and Delete offered **in the app** (owner's direction), each behind the warnings above? | **Yes**: Restore, Archive (verified copy; the parked files stay) and Delete (typed word, ticks, repeated checks) |
| 5 | Whole albums only (an album with one unverified file is not retired at all)? | **Yes** |
| 6 | Retire byte-identical duplicate albums too (their kept twin verified)? | **Yes**, shown separately in the dry run |
| 7 | Archive = a **verified copy** (parked originals stay until deleted), or a **move** to the archive? | **Copy**: one action per purpose, nothing lost if the archive drive fails; Delete then frees the space |
| 8 | A cooling-off period before Delete: 7 days, then an extra tick before then? | **Yes**, 7 days, user-adjustable; not a block, a second look |
| 9 | Typed confirmation for Delete (the word DELETE)? | **Yes** |

## Consequences

* The product can take a user from a messy collection to **one organized library**, in steps they can reverse,
  and finally — their decision, with warnings — free the space.
* "Sources are read-only" gains two tightly fenced exceptions (retire = move; confirmed Delete inside
  `_ORIGINALS`); the review guideline and the tests encode their limits.
* More to build than a bare move: the labelling files, an Originals screen, a verified archive, a guarded delete.
  Build order: retire + restore + labelling first; archive next; delete last, after real use of the others.
* The scanner learns one more ignored folder; the database gets `retired` and `archive` tables.
