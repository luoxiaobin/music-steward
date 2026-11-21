# ADR 0003: Retire the originals — a guided, verified, reversible step

* **Status:** Proposed (2026-11-21). The owner wants the feature (strategy review, 2026-11-20); the design below
  needs the owner's answers to the **open decisions** before any code is written.
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
* Non-goal (first release): freeing disk space. Retiring **moves** files; space comes back only when the user
  deletes what was moved. In-app deletion is a later, separate step (phase 2 below).
* Non-goal: acting on anything that was skipped, undecided, blocked, flagged "Audio changed", or not part of a
  verified run.

## Decision (proposed)

**Retire = move, never delete.** A retired original is moved, with its relative path, into a holding folder on the
**same volume**: `<root>/_mlt_retired/<run id>/<original relative path>`. A rename on the same share is instant,
atomic and keeps every byte; nothing is read or rewritten. **Restore** moves it back. The app never deletes
anything in this release.

### What may be retired (all must hold, checked again when the user confirms)

| Check | Why | How |
|---|---|---|
| The file belongs to an album whose **whole plan item set is committed** in a run that is **not undone** | no half-organized album loses its originals | the run's journal: every item of the unit `committed` (own copy) or `already_existed` (an identical file was there) |
| The **organized copy still exists** and its **audio is unchanged**: audio fingerprint equals the journal's `src_audio` | proves the music itself is safe at the destination, also if the user retagged the copy since | `hashing.copy_audio_fingerprint` on the destination (a local disk: fast) |
| The **source is unchanged** since it was copied: size, modification time and content identity (ADR 0002) equal what the run recorded | never retire a file that is not the one that was verified | `os.stat` + `identity.audio_identity` |
| Split albums (cue image → tracks): **every track** of the image verified, then the image and its cue sheet may go | the image is only redundant once all its tracks are safe | all `split` items of that image `committed` |
| Side files (cue, lyrics, covers) of the album that were copied | small; a copy changed by the user is **flagged**, not blocking (the original stays reachable in the retired folder) | sha of the copy vs. the journal |
| Everything stays **inside the root**, on the **same device**, and no path or folder on the way is a symlink | the exception must not become a way out of the collection | `realpath` checks like apply's `_guard`; `st_dev` equal; refuse when `_mlt_retired` is a link |

Anything else in a source folder — files that were never part of the plan, other albums, notes — **stays where it
is**. Only the files of verified albums move, one by one, mirroring their paths. A folder is never moved as a
whole.

### Process

1. **Dry run first, always**: *Retire originals…* lists, per album, "can retire" or "can't, and why" (first failing
   check), with counts and sizes. Nothing changes.
2. **One explicit confirmation** that names what happens: "Move 118 albums (412 GB) into `/…/_mlt_retired/7/` on
   the same share. Nothing is deleted. You can restore them." Plus the redundancy acknowledgement (below).
3. **Journal-first moves**: per file, an intent record (source, target, identity), then `rename`, then a
   `moved` record — the same discipline as apply. A crash leaves every file either at its old path or at the
   new one, never in between; **Resume** finishes the batch; each file's checks are repeated before its move.
4. **Restore** (per album, or the whole batch): moves back what is still there, refuses to overwrite anything
   that has appeared at the original path since (reports it), and tells which files are no longer in the retired
   folder.
5. Retired files are recorded in the app database (a `retired` table keyed by the file's identity) so the UI shows
   "Retired", and a later scan does not treat them as new or missing. The scanner **ignores `_mlt_retired`**
   (like junk folders), so the moved files are not rediscovered as albums.

### Opt-in and the rule in AGENTS.md

* **Off by default, per root.** Settings → *Allow retiring originals in this collection folder* (a checkbox per
  root, with a short explanation). Without it the code path is unreachable and "sources are read-only" holds
  exactly as today.
* AGENTS.md P0 changes from "any code path that could write, rename … under a configured root" to: **only
  `retire` may rename files under a configured root, only into `<root>/_mlt_retired/`, only for files that
  passed the checks above; any other write, rename, truncation or deletion under a root, and any deletion
  at all, is P0.** Codex and reviewers then enforce the narrower rule.

### The redundancy warning (the one risk this feature adds)

Today there are two copies. After retiring there is **one organized copy** (the originals are only parked).
Deleting the parked originals later makes the organized copy the *only* copy, on whatever disk the destination
is — often a single external drive. The confirmation therefore says it plainly and asks for a tick: *"I know the
organized copy will be the only copy once I delete the retired originals, and I have (or will make) a backup."*
The app does not make backups; it will say so.

### Space, and what a later deletion must do (phase 2, its own decision)

Space is freed only by deleting `_mlt_retired`. In phase 1 the user does that themselves (Finder, after looking).
A later **Empty retired** step would: work per batch and only for batches older than a number of days the user
sets; re-verify every album's copy again just before deleting; list what is **not** carried over to the copies
(originals' tags that were repaired, original cue sheets of split albums, files that were never copied) and
require a typed confirmation; never delete a file whose copy no longer verifies. Not built until phase 1 has
been used on real albums.

## Alternatives considered

| Option | Why not |
|---|---|
| Delete the originals after verifying | irreversible; a verification bug becomes data loss. Rejected for phase 1. |
| macOS Trash | SMB shares have no Trash the app can rely on; emptying the Trash loses the path needed to restore; the Synology `#recycle` folder is ignored by the scanner and not under our control. |
| Move whole folders | would take along albums that were not copied or verified. Rejected: per-file moves of verified albums only. |
| A "move" mode in apply (copy, verify, delete) | removes the review between *copy* and *originals gone*; one action, no second look. Rejected. |
| Retire to another volume | a copy across volumes is a second 3 TB transfer, and a crash leaves two half-states. Same-volume rename only. |

## Failure modes to test (failure injection, like apply)

* crash between the intent and the rename, between the rename and the `moved` record, mid-batch; Resume and
  Restore each leave every file in exactly one place;
* a source changed after the dry run (size, mtime, content) is skipped, not moved;
* the copy's audio changed or the copy deleted after the dry run: its album is skipped;
* a symlink planted at `_mlt_retired`, at a parent folder, or at a source path: refused, nothing moved;
* the target path already exists: nothing overwritten (`rename` without replace);
* the run was undone after the dry run: its albums are skipped;
* restore when the original path is occupied: reported, nothing overwritten;
* never retires: skipped / undecided / blocked / "Audio changed" albums, albums with a failed or missing item,
  files outside the verified albums;
* a root without the opt-in: every entry point refuses.

## Open decisions for the owner

| # | Question | Recommendation |
|---|---|---|
| 1 | Opt-in per collection folder, off by default? | **Yes** |
| 2 | Require the redundancy tick (organized copy becomes the only copy once the parked originals are deleted)? | **Yes** |
| 3 | Where do retired originals go? | **`<root>/_mlt_retired/<run>/…`**, same volume, instant, reversible (a configurable same-volume folder is possible but adds ways to get it wrong) |
| 4 | Phase 1 without in-app deletion — you delete `_mlt_retired` yourself when satisfied? | **Yes**; add "Empty retired" only after real use |
| 5 | Whole albums only (an album with one unverified file is not retired at all)? | **Yes** |
| 6 | Retire byte-identical duplicate albums too (their kept twin verified)? | **Yes**, shown separately in the dry run |

## Consequences

* The product can take a user from a messy collection to **one organized library**, in steps they can reverse.
* "Sources are read-only" gains one tightly fenced exception; the review guideline and the tests encode its limits.
* Phase 1 frees no space by itself: the honest cost of never deleting automatically.
* The scanner learns one more ignored folder; the database gets a `retired` table.
