# Recovery and safety

What Music Steward guarantees, what it does not, and what to do when something is interrupted.

## The short version

* **Your collection is only read.** No command writes, renames or deletes anything under a collection folder, and a
  destination inside (or containing) a collection folder is refused when the plan is built.
* **Only Apply writes**, and only into the destination folder. A file is copied under a temporary name, tagged,
  verified, and only then given its final name.
* **Nothing at the destination is overwritten** — with one documented exception on NAS shares, below.
* **Interrupted? Resume, don't apply again.** **Undo** removes exactly what a run created, if you haven't changed it.

## Stopping and interruptions

| What happened | What is left | What to do |
|---|---|---|
| You pressed **Stop** while recognizing | Finished albums keep their results; the rest stay *queued* | **Recognize folder** again later continues with the queued ones |
| You pressed **Stop** while applying | Finished files are in place; nothing half-written under a final name | **Resume** on the plan screen (or `music-steward resume <run>`) |
| The app quit, crashed, or the Mac lost power | Recognition: the album being matched is redone next time. Apply: possibly one temporary file `.<name>.mlt-<run>-<op>.tmp…` in the destination | Apply: **Resume** — it reads the journal, cleans up temporary files and continues |
| The NAS or drive disconnected during a **scan** | The scan stops and marks **nothing** as removed | Reconnect, scan again |
| …during **recognition** | Affected albums show *Error* with the reason | Reconnect, **Recognize folder** again (errors are retried) |
| …during **apply** | Affected files are marked failed; no partial file under a final name | Reconnect, **Resume** |

Tested on 329 albums of a real collection: Ctrl-C and a hard kill during recognition, both resumed with **0 albums
redone and 0 left stuck**; Ctrl-C and a hard kill during a 14 GB apply, both resumed, with **every source file
unchanged** and undo removing every copy (see [PLAN.md item 19](PLAN.md)).

### Resume, not Apply again

After an interruption use **Resume** on that run. Applying the same plan again starts a *second* run: files the first
run already published are reported as "already exists" (identical) or blocked (different), which is safe but
confusing, and undo then needs both runs.

## Conflicts are reported, never resolved by overwriting

The plan marks these items **Blocked**; Apply skips them:

| Reason | Fix |
|---|---|
| *a file already exists at the destination* | Choose an empty destination, or remove that file yourself if it is an old copy |
| *another item in this plan has the same destination name* | Two different albums would get the same name — open one in review, fix its name (Edit fields) or use own info, build the plan again |
| *source changed or missing since the scan* | Re-scan that folder, build the plan again |

Byte-identical duplicate albums in your collection are not a conflict: the plan copies one and lists the others under
"Duplicate albums copied once".

## Undo

**Undo run** (plan screen) or `music-steward undo <run>` removes each copy the run **created**, only if it is still
**unchanged** (same content hash and same file identity). A copy you edited, retagged or replaced is **kept** and
reported. Files the run did not create are never touched. Empty folders may remain.

## What is guaranteed, and how it was tested

| Guarantee | How | Evidence |
|---|---|---|
| Sources are never written | All writes go through the apply protocol into the destination; the destination may not overlap a collection root | tests; every trial checked all source sizes and modification times afterwards |
| No partial file under a final name | Copy to a temporary name → tag → verify → publish; journal on the local disk first | crash tests at every step (`m0/EVIDENCE_REPORT.md` §6) |
| Audio unchanged in the copies | After tagging, the copy's audio bytes (APE/WavPack/TTA) or audio packets (other formats) must equal the original's | tests; 1,800+ files in trials |
| No overwrite at the destination (local disk) | Atomic exclusive rename (`renamex_np(RENAME_EXCL)` on APFS) or `link()+unlink()` | crash and race tests |
| Undo removes only our unchanged copies | Content hash + file identity recorded at publish | tests; trials |

**Not covered:** the app does not make backups of your collection, and it cannot protect copies from later disk
failure. Keep your own backups.

## Writing the organized copy to a NAS

The supported destination is a **local or external disk**. Writing the organized copy *to a NAS share* is
**experimental**: SMB shares (tested: Synology, SMB 2) support neither atomic exclusive rename nor hard links, so the
app uses **claim-then-rename**: it first creates the final name as an empty placeholder (which fails if anything is
there), re-checks it, then renames the finished copy over its own placeholder.

Residual limitations ([evidence report §6.7](../m0/EVIDENCE_REPORT.md)):

* If **another program** writes a file with the **same name in the same folder** in the microseconds between the
  re-check and the rename, that file would be overwritten. Don't let other programs write into the destination
  during an apply.
* A crash between creating the placeholder and recording it leaves a 0-byte file whose ownership is decided by its
  time stamp rather than its file identity; Resume removes it.

## Backing up the app's own data

Everything the app knows lives in `~/.music-steward/`. To back it up or move it to another Mac, **quit the app** and
copy the folder as a whole — the database (`library.sqlite`) and the apply journals (`journal/`) belong together:
Resume and Undo need both. The caches (`http-cache.sqlite`, `preview/`) can be deleted at any time; they only make
things faster. See [Configuration — data locations](CONFIGURATION.md#where-the-app-keeps-its-data).
