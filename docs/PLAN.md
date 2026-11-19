# MP3LibraryTool — Plan

Living plan. Sources: the Architecture Assessment and M0 Experiments specs (Nov 18, 2026), `m0/EVIDENCE_REPORT.md`, and the decisions below.

## Principles

* Sources are read-only; changes go only to organized copies, from a saved plan.
* Every apply is journalled: crash-safe, resumable, undoable (only what the tool created).
* Local, free, offline except recognition. Never commit real music.

## Milestones

| | Delivers | Status |
|---|---|---|
| **M0** Experiments | beets as a library; safe copy/tag on the NAS | ✅ done — `m0/EVIDENCE_REPORT.md` |
| **M1** Inventory and audit | `scan` / `hash` / `audit` / `inspect`; incremental, crash-safe, read-only | 🔄 code done; full NAS scan + re-scan check in progress |
| **M2** Recognition and review | background matching queue; review UI; destination preview; conflicts; **desktop window (pywebview)** | 🔄 read-only UI done |
| **M3** Apply, verify, recover | saved plans; copy → tag → verify → publish; journal; resume; undo | protocol proven in M0 |
| **M4** Apple export | FLAC/APE → ALAC tree for iPhone; MP3 copied | later |

## M2 order of work

1. ✅ `serve` + read-only inventory browser (three-pane, per the Review & Plan mockups)
2. Recognition queue: rate-limited (MusicBrainz ~1/s, AcoustID 3/s), resumable, cached; one worker process per job (ADR-level finding F1)
3. Pre-processing before matching: GBK/Big5 tag repair; cue-sheet albums; tag-sanity fallbacks
4. Own confidence rule (AcoustID-linked + distance < ~0.15 → Ready); artist agreement for singles
5. Extra candidate sources for mainland releases (evaluate Discogs, Deezer)
6. Review actions: candidates with evidence, Accept / Edit / Skip / Search again; decisions saved immediately
7. Destination preview from the naming template; casefold/NFC collision and existing-file conflicts
8. **Desktop window with pywebview + native folder picker** — [ADR 0001](adr/0001-desktop-shell-pywebview.md)

## Decisions

| Date | Decision | Record |
|---|---|---|
| 2026-11-18 | NAS publish method: `claim_rename` (O_EXCL placeholder, then rename); local disk keeps `RENAME_EXCL` | `m0/EVIDENCE_REPORT.md` §6.7 |
| 2026-11-18 | Desktop shell: pywebview at the end of M2; not Electron | [ADR 0001](adr/0001-desktop-shell-pywebview.md) |
| 2026-11-18 | License GPL-3.0-or-later | `LICENSE` |

## Scale (measured)

* NAS share: **~157,000 files** (assessment assumed ~2,000) — targets in the assessment must be re-set once the M1 NAS scan completes.
