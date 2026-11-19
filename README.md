# MP3LibraryTool

Organize a large music collection (MP3, FLAC, APE, …) safely: **scan → recognize → review → plan → apply**.
Sources are never modified; changes are written only to organized copies, from a saved plan, with a journal for crash recovery and undo.

Status: **M1 — inventory and audit.** M0 experiments and their evidence report are in [`m0/`](m0/EVIDENCE_REPORT.md).

## Install

Requires Python 3.12+ and FFmpeg (`ffmpeg`, `ffprobe`) on `PATH`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Use

All commands only **read** the collection. They write a single app database (default `~/.mp3librarytool/library.sqlite`, override with `--db` or `MLT_DB`).

```bash
mp3librarytool scan /Volumes/music          # inventory: paths, sizes, ffprobe properties, tags
mp3librarytool scan /Volumes/music          # again: unchanged files (size + mtime) are skipped
mp3librarytool hash --root /Volumes/music   # whole-file + audio-stream hashes (resumable; reads every byte)
mp3librarytool audit                        # summary of all audit filters
mp3librarytool audit garbled_tags           # one filter: unreadable, missing_metadata, garbled_tags,
                                            #   estimated_values, exact_duplicates, same_audio, orphan_lrc,
                                            #   cue_sheets, dirs_named_like_file, unlistable_dirs, removed
mp3librarytool inspect "song.mp3" --hash    # one file, no database
```

### What the scan guarantees

* No folder cap; file vs folder is decided by the filesystem, never by extension (`Album.flac/` folders exist).
* Paths are stored NFC-normalized (macOS may return NFD).
* Unreadable files are listed, not fatal. Files vanishing mid-scan are handled.
* If the root disappears mid-scan (unplugged drive, dropped share), the scan **aborts and marks nothing removed**.
* Audio hash ignores tags: FFmpeg packet hash for MP3/FLAC/M4A/WAV; decoded-PCM hash for APE (its demuxer folds trailing tags into the last packet).
* Garbled GBK/Big5 tags get a **repair proposal**; nothing is written.

## Tests

```bash
.venv/bin/python -m pytest
MLT_APE_SAMPLE=/path/to/any.ape .venv/bin/python -m pytest   # also run the APE test
```

Fixtures are generated with FFmpeg at test time. **Never commit real music** to this repository.

## License

GPL-3.0-or-later (see `LICENSE`). beets/mutagen, used in later milestones, are GPL-compatible.
