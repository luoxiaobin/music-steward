"""Measure the cost of file identities on the collection (ADR 0002). READ-ONLY.

For a random sample of audio files from the app database, time:
  full     -- hashing.audio_hash (FFmpeg packet hash; PCM decode for APE/WavPack/TTA): the exact identity
  sampled  -- length of the audio region + SHA-256 of a few 64 KiB slices inside it (tags excluded):
              a cheap provisional identity, independent of tag edits

Prints aggregates only (no paths), so the output can be quoted without listing the collection.

    python tools/identity_bench.py --db ~/.music-steward/library.sqlite --n 40 [--seed 1]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sqlite3
import statistics
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from music_steward.hashing import HashError, audio_hash  # noqa: E402
from music_steward.probe import audio_offset  # noqa: E402
from music_steward.tags import apev2_span  # noqa: E402

SLICE = 64 * 1024
FRACTIONS = (0.0, 0.25, 0.5, 0.75)        # where the slices start, as a fraction of the audio region


def flac_audio_start(fh) -> int:
    """End of the FLAC metadata blocks (tags, pictures, padding live there)."""
    fh.seek(0)
    head = fh.read(4)
    pos = 0
    if head[:3] == b"ID3":                 # rare: an ID3v2 tag in front of fLaC
        fh.seek(6)
        b = fh.read(4)
        pos = 10 + ((b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3])
        fh.seek(pos)
        head = fh.read(4)
    if head != b"fLaC":
        return 0
    pos += 4
    while True:
        fh.seek(pos)
        h = fh.read(4)
        if len(h) < 4:
            return pos
        last, length = h[0] & 0x80, struct.unpack(">I", b"\0" + h[1:])[0]
        pos += 4 + length
        if last:
            return pos


def id3v2_end(fh) -> int:
    fh.seek(0)
    h = fh.read(10)
    if h[:3] != b"ID3" or len(h) < 10:
        return 0
    return 10 + ((h[6] << 21) | (h[7] << 14) | (h[8] << 7) | h[9]) + (10 if h[5] & 0x10 else 0)


def audio_region(path: str, ext: str, codec: str | None) -> tuple[int, int]:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if codec == "flac" or ext == ".flac":
            start = flac_audio_start(fh)
        elif codec in ("ape", "wavpack", "tta"):
            start = audio_offset(path, ext) or 0
        else:
            start = id3v2_end(fh)
        end = size
        fh.seek(max(0, end - 128))
        if fh.read(3) == b"TAG":
            end -= 128
    span = apev2_span(path)
    if span and span[0] >= start:
        end = min(end, span[0])
    return start, end


def sampled_identity(path: str, ext: str, codec: str | None) -> str:
    start, end = audio_region(path, ext, codec)
    n = end - start
    h = hashlib.sha256(str(n).encode())
    with open(path, "rb") as fh:
        for f in FRACTIONS:
            pos = start + int(n * f)
            fh.seek(pos)
            h.update(fh.read(min(SLICE, end - pos)))     # never past the audio into trailing tags
        pos = max(start, end - SLICE)                   # the last slice, ending where the audio ends
        fh.seek(pos)
        h.update(fh.read(end - pos))
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.music-steward/library.sqlite"))
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-full", action="store_true", help="time the sampled identity only")
    ap.add_argument("--edges", action="store_true", help="slices at the start and end of the audio only")
    ap.add_argument("--workers", type=int, default=1, help="with --no-full: files read at once (wall time)")
    a = ap.parse_args()
    if a.edges:
        global FRACTIONS
        FRACTIONS = (0.0,)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT f.relpath, f.fs_relpath, f.ext, f.codec, f.size, r.path AS root FROM file f "
                       "JOIN root r ON r.id=f.root_id WHERE f.removed_at IS NULL AND f.kind='audio' "
                       "AND f.probe_status='ok'").fetchall()
    total_bytes = {}
    for r in rows:
        k = r["codec"] or r["ext"]
        total_bytes[k] = total_bytes.get(k, 0) + r["size"]
    rnd = random.Random(a.seed)
    sample = rnd.sample(rows, min(a.n, len(rows)))
    if a.no_full and a.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        t = time.perf_counter()
        with ThreadPoolExecutor(a.workers) as ex:
            got = list(ex.map(lambda r: sampled_identity(os.path.join(r["root"], r["fs_relpath"]), r["ext"], r["codec"]),
                              sample))
        wall = time.perf_counter() - t
        print(f"sampled, {a.workers} at a time: {len(sample)} files in {wall:.1f} s = {wall / len(sample) * 1000:.0f} ms/file; "
              f"distinct: {len(set(got))}; whole collection ≈ {wall / len(sample) * len(rows) / 3600:.1f} h")
        return 0
    stats: dict[str, dict[str, list]] = {}
    ids: dict[str, int] = {}
    for i, r in enumerate(sample, 1):
        p = os.path.join(r["root"], r["fs_relpath"])
        k = r["codec"] or r["ext"]
        s = stats.setdefault(k, {"bytes": [], "sampled_s": [], "full_s": []})
        s["bytes"].append(r["size"])
        t = time.perf_counter()
        sid = sampled_identity(p, r["ext"], r["codec"])
        s["sampled_s"].append(time.perf_counter() - t)
        ids[sid] = ids.get(sid, 0) + 1
        if not a.no_full:
            t = time.perf_counter()
            try:
                audio_hash(p, r["ext"], codec=r["codec"])
                s["full_s"].append(time.perf_counter() - t)
            except HashError:
                pass
        print(f"\r{i}/{len(sample)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    print(f"sample: {len(sample)} files of {len(rows)}; distinct sampled identities: {len(ids)}")
    print(f"{'codec':10} {'n':>4} {'avg MB':>8} {'sampled ms':>11} {'full s':>8} {'full MB/s':>10}")
    est_sampled = est_full = 0.0
    n_by = {}
    for r in rows:
        k = r["codec"] or r["ext"]
        n_by[k] = n_by.get(k, 0) + 1
    for k, s in sorted(stats.items(), key=lambda x: -total_bytes.get(x[0], 0)):
        mb = statistics.mean(s["bytes"]) / 1e6
        sm = statistics.median(s["sampled_s"]) * 1000
        fs = statistics.mean(s["full_s"]) if s["full_s"] else float("nan")
        rate = sum(s["bytes"][: len(s["full_s"])]) / 1e6 / sum(s["full_s"]) if s["full_s"] else float("nan")
        print(f"{k:10} {len(s['bytes']):4} {mb:8.1f} {sm:11.0f} {fs:8.2f} {rate:10.1f}")
        est_sampled += n_by[k] * statistics.mean(s["sampled_s"])
        if s["full_s"]:
            est_full += total_bytes[k] / 1e6 / rate
    print(f"whole collection, extrapolated from the sampled codecs: sampled ≈ {est_sampled / 3600:.1f} h, "
          f"full ≈ {est_full / 3600:.1f} h (one at a time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
