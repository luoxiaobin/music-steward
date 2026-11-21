# ADR 0002: Album identity independent of file paths

* **Status:** Proposed. Direction accepted 2026-11-20; the design below is settled when it is implemented.
* **Date:** 2026-11-20
* **Deciders:** Kevin Luo

## Context

Music Steward should support **ongoing upkeep**, not only a one-time cleanup: the user adds, renames and moves music
over the years, re-scans, and expects earlier decisions (accepted matches, own info, edits, skips) to stay with the
right album.

Today an album's identity is built from paths:

* a file is identified by `(root, relpath)`; a renamed or moved file looks like a new file plus a removed one;
* a recognition unit's `key` is built from the folder or the cue sheet and audio file paths (`recognize/units.py`);
* decisions are attached to the unit.

So renaming a folder discards its decisions. The opposite also happens: a file whose **content** changes under the
same path keeps the same identity, and its earlier accepted match is kept (strategy review 2026-11-20, defect 3).

## Decision (direction)

Identity follows the **audio**, and paths are only the current location:

1. **File identity**: the tag-independent audio hash (`hashing.audio_hash`), so retagging a file does not change it
   and re-encoding does. A re-scan that finds a new path with a known identity treats it as a move.
2. **Album identity**: derived from its members' identities: the sorted set of file identities for an album of track
   files; the image's identity plus the cue track layout for a cue-sheet album; per disc for sets.
3. **Decisions attach to the album identity.** A path change keeps them. A content change (different identity)
   sends the album back to review and says why ("the files changed since you decided"); a decision is never applied
   silently to different audio.
4. The organized copies' journal already records the source and copy hashes, so a later run can tell "already
   organized" from "new" by identity.

## Open questions (to settle when implementing)

* **Hashing cost.** A full audio hash reads every byte (3 TB over SMB), and the 2026-11-18 decision is *no
  whole-collection hashing*. Candidates: a cheap provisional identity (size + hashes of a few fixed slices) at scan
  time, promoted to the full audio hash when the album is recognized, planned or applied, which already reads it.
  To be measured on the NAS.
* Partial changes (a track added to or removed from an album): a new album identity that inherits the decision for
  review, or a review prompt.
* Byte-identical duplicates in two folders: one identity, two locations, which matches "copied once".
* Migration of existing units and decisions.

## Consequences

* Upkeep (re-scan, organize only what is new or changed) becomes possible without re-reviewing.
* Fixes defect 3 by design instead of a separate patch.
* Scanning gains a hashing step whose cost must stay acceptable on a NAS.
