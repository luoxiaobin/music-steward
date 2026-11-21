# ADR 0002: Album identity independent of file paths

* **Status:** Proposed. Direction accepted 2026-11-20; cost measured 2026-11-20; design below awaits the owner's OK.
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

* ~~Hashing cost~~ — **measured 2026-11-20** (below).
* Partial changes (a track added to or removed from an album): a new album identity that inherits the decision for
  review, or a review prompt.
* Byte-identical duplicates in two folders: one identity, two locations, which matches "copied once".
* Migration of existing units and decisions.

## Measurements (2026-11-20, `tools/identity_bench.py`, NAS over SMB from the Mac, read-only)

Collection: 142,148 audio files, 3.04 TB (1.9 TB FLAC, 0.6 TB MP3, 0.5 TB APE). The first full scan (properties +
tags, files read in folder order, in parallel) took 1.25 h, about 30 ms per file.

| Identity | How it reads | Per file | Whole collection |
|---|---|---|---|
| Full audio hash (`hashing.audio_hash`; APE decoded) | every byte | FLAC 60 MB/s, MP3 42 MB/s, APE 18 MB/s | **≈ 20.6 h** |
| Sampled: audio length + 5 slices of 64 KiB through the audio, random files | 3 opens, 5 seeks | ~200 ms (8 or 16 at a time: no faster) | 7.6–9.6 h |
| Edges: audio length + first and last 64 KiB of the audio, random files | start and end | 110–170 ms | 4.4–6.5 h |
| **Edges, files in folder order, 8 at a time** | start and end | **~50 ms** | **≈ 2 h** |
| Floor: one open, read first and last 256 KiB, folder order, 8 at a time | — | ~50 ms | ≈ 2 h |

Findings: the cost on the NAS is seeks, not bytes; reading in the middle of files and reading files in random order
are what make it slow, and parallelism does not help random reads. Read in folder order, the edges identity costs no
more than opening the file and reading its ends — the same regions the scan already reads for tags. All 840 sampled
files had distinct identities; the identity stayed the same under tag edits that moved the audio by 300 KB (FLAC
padding exceeded, MP3 with a large picture).

## Design (proposed from the measurements)

1. **Provisional file identity, computed by the scan** for new and changed files only (re-scans of unchanged files
   cost nothing): SHA-256 of the audio region's length, its first 64 KiB and its last 64 KiB. The audio region
   excludes ID3v2 / FLAC metadata blocks in front and APEv2 / ID3v1 at the end, so tag edits do not change it.
   Expected cost: on the order of the first scan again (≈ 1–2 h for 3 TB), once.
2. **Confirmed identity, the full audio hash, only where the bytes are read anyway**: when an album is copied by
   Apply (the copy reads every byte) and for duplicate checks the user asks for. Never a whole-collection hash.
3. Limitation, accepted: a change confined to the middle of the audio (same length, same first and last 64 KiB) keeps
   the identity. The scan still sees the file as changed (size or date), and the confirmed hash at Apply catches it
   before a copy is made from a stale decision.

## Consequences

* Upkeep (re-scan, organize only what is new or changed) becomes possible without re-reviewing.
* Fixes defect 3 by design instead of a separate patch.
* Scanning gains a hashing step whose cost must stay acceptable on a NAS.
