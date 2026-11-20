# Music Steward — notes for coding agents and reviewers

A careful steward for a large music collection: **scan → recognize → review → plan → apply**. Python 3.12 engine
(beets in-process for matching, mutagen for tags, FFmpeg), SQLite app database, plain HTML/JS UI served on 127.0.0.1,
optionally in a pywebview desktop window. Background and decisions: `docs/PLAN.md`, `docs/FEATURE_SHEET.md`,
`docs/adr/`.

## Build and test

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # needs ffmpeg/ffprobe on PATH
.venv/bin/python -m pytest -q                                     # fixtures are generated with FFmpeg
for f in music_steward/web/*.js; do node --check "$f"; done
```

Never commit real music, collection listings, API keys or tokens.

## Review guidelines

Review against the user's journey: *a cautious owner with a 3 TB NAS wants an organized copy without ever risking the
originals.* Treat these as **P0** (block the PR):

- **Sources are read-only.** Any code path that could write, rename, truncate or delete under a configured root, or
  let a destination overlap a root (`apply/plan.py: check_destination`).
- **No overwrite at the destination.** Publishing must stay no-overwrite (`apply/protocol.py`: RENAME_EXCL / link /
  claim-then-rename); conflicts are reported, never resolved by replacing a file.
- **Crash safety.** Apply is journal-first; an interrupted scan, recognition or apply must leave nothing half-written
  under a final name and must be resumable; undo removes only copies that are unchanged (sha + inode).
- **Audio integrity.** Tag writes go to copies only and must be followed by the audio check
  (`hashing.copy_audio_fingerprint`).
- **Privacy.** API keys and tokens never logged, stored, sent in URLs, or committed; the HTTP cache keys requests by a
  hash; the server binds 127.0.0.1 with the token cookie, Host check, CSP and the `X-MLT` POST guard.

Also flag (P1): user decisions silently changed by a re-match or a bulk action (bulk actions touch undecided albums,
or exactly the selected ones); data (album titles, paths, tags) being translated by the UI translator
(`web/i18n.js` must only match interface text); Chinese text handling (GBK/Big5 repair, Simplified/Traditional,
NFC) regressions; missing tests for new behaviour; user-visible changes without a `CHANGELOG.md` entry.

Style: small focused modules, comments explain *why* (often a measured finding on the real collection), tests
generate their own audio with FFmpeg.
