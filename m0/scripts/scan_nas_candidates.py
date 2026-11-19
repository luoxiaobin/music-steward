#!/usr/bin/env python3
"""Read-only scan of the NAS share for M0 sample-folder candidates.

Only os.scandir / stat / reading tags. Never opens anything for writing on
the share. Output: m0/scratch/nas_candidates.json
"""
import json
import os
import re
import sys
import time

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/Volumes/music"
MAX_DEPTH = 4
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch", "nas_candidates.json")
AUDIO = (".mp3", ".flac", ".ape", ".wav", ".m4a", ".wma")
SKIP = {"_mlt_scratch", "#recycle", "@eaDir", ".DS_Store"}

LIVE = re.compile(r"live|演唱会|演唱會|现场|現場|concert|音乐会|音樂會", re.I)
REMASTER = re.compile(r"remaster|重制|重製|复刻|復刻|24bit|xrcd|k2hd|sacd|dsd|hdcd|首版|再版", re.I)
COMPIL = re.compile(r"群星|合辑|合輯|精选|精選|various|\bva\b|金曲|经典|經典|发烧|發燒|test ?cd|试音|試音", re.I)
# characters that exist only in Traditional Chinese (simplified forms differ)
TRAD = re.compile(r"[鄧麗張陳劉華樂聲愛國東歲憶經與龍鳳電們這說話們來時間無長開關門見風雲夢後會]")
SIMP = re.compile(r"[邓丽张陈刘华乐声爱国东岁忆经与龙凤电们这说话来时间无长开关门见风云梦后会]")

found = {"lrc": [], "live": [], "remaster": [], "compilation": [], "traditional": [],
         "cue": [], "dir_named_like_file": []}
audio_dirs = {}
t0 = time.time()


def walk(d, depth):
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    n_audio = 0
    for e in entries:
        if e.name in SKIP or e.name.startswith("."):
            continue
        try:
            is_dir = e.is_dir(follow_symlinks=False)
        except OSError:
            continue
        rel = os.path.relpath(e.path, ROOT)
        low = e.name.lower()
        if is_dir:
            if low.endswith(AUDIO):
                found["dir_named_like_file"].append(rel)
            for key, rx in (("live", LIVE), ("remaster", REMASTER), ("compilation", COMPIL)):
                if rx.search(e.name):
                    found[key].append(rel)
            if TRAD.search(e.name) and not SIMP.search(e.name):
                found["traditional"].append(rel)
            if depth < MAX_DEPTH:
                walk(e.path, depth + 1)
        else:
            if low.endswith(".lrc"):
                found["lrc"].append(rel)
            elif low.endswith(".cue"):
                found["cue"].append(rel)
            elif low.endswith(AUDIO):
                n_audio += 1
    if n_audio:
        audio_dirs[os.path.relpath(d, ROOT)] = n_audio


walk(ROOT, 1)
found["audio_dirs_count"] = len(audio_dirs)
found["scan_seconds"] = round(time.time() - t0, 1)

# --- garbled-tag probe: ID3 text stored as Latin-1 but really GBK/Big5 --------
from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: E402

garbled = []
checked = 0
for d in sorted(audio_dirs):
    if checked >= 400 or len(garbled) >= 25:
        break
    full = os.path.join(ROOT, d)
    try:
        mp3s = sorted(f for f in os.listdir(full) if f.lower().endswith(".mp3"))[:2]
    except OSError:
        continue
    for f in mp3s:
        checked += 1
        try:
            tags = ID3(os.path.join(full, f))
        except (ID3NoHeaderError, Exception):
            continue
        for fid in ("TIT2", "TPE1", "TALB"):
            fr = tags.get(fid)
            if not fr or fr.encoding != 0:  # 0 = Latin-1
                continue
            text = str(fr.text[0]) if fr.text else ""
            if any(0x80 <= ord(c) <= 0xFF for c in text):
                raw = text.encode("latin-1", "replace")
                guess = {}
                for enc in ("gbk", "big5"):
                    try:
                        guess[enc] = raw.decode(enc)
                    except UnicodeDecodeError:
                        pass
                garbled.append({"dir": d, "file": f, "frame": fid, "shown_as": text, "decodes_as": guess})
                break
found["garbled_tags"] = garbled
found["mp3_files_checked_for_garbled_tags"] = checked
found["total_seconds"] = round(time.time() - t0, 1)

with open(OUT, "w") as fh:
    json.dump(found, fh, ensure_ascii=False, indent=1)
print({k: (len(v) if isinstance(v, list) else v) for k, v in found.items()})
