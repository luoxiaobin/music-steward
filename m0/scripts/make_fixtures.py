#!/usr/bin/env python3
"""
Generate synthetic scratch fixtures for M0a/M0b experiments.
NEVER touches the real collection or NAS — everything here is
freshly synthesized tone audio with mutagen-applied tags.

Run: venv/bin/python scripts/make_fixtures.py
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "fixtures")

from mutagen.id3 import (
    ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TRCK, TPOS, TCOM, TCON,
    TDRC, APIC, USLT, TXXX,
)
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3


def tone_wav(path, freq=440, seconds=2):
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        path,
    ], check=True)


def make_mp3(path, freq=440, seconds=2):
    tmp = path + ".wav"
    tone_wav(tmp, freq, seconds)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
        "-codec:a", "libmp3lame", "-qscale:a", "2", path,
    ], check=True)
    os.remove(tmp)


def make_flac(path, freq=440, seconds=2):
    tmp = path + ".wav"
    tone_wav(tmp, freq, seconds)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
        "-codec:a", "flac", path,
    ], check=True)
    os.remove(tmp)


def tag_mp3(path, **kw):
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    if "title" in kw:
        id3.add(TIT2(encoding=3, text=kw["title"]))
    if "artist" in kw:
        id3.add(TPE1(encoding=3, text=kw["artist"]))
    if "album" in kw:
        id3.add(TALB(encoding=3, text=kw["album"]))
    if "track" in kw:
        id3.add(TRCK(encoding=3, text=str(kw["track"])))
    if "disc" in kw:
        id3.add(TPOS(encoding=3, text=str(kw["disc"])))
    if "date" in kw:
        id3.add(TDRC(encoding=3, text=kw["date"]))
    if "genre" in kw:
        id3.add(TCON(encoding=3, text=kw["genre"]))
    if "lyrics" in kw:
        id3.add(USLT(encoding=3, lang="eng", desc="", text=kw["lyrics"]))
    if "art" in kw:
        id3.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=kw["art"]))
    if "replaygain" in kw:
        id3.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=kw["replaygain"]))
    id3.save(path, v2_version=3)


def tag_flac(path, **kw):
    f = FLAC(path)
    for k in ("title", "artist", "album", "genre", "date"):
        if k in kw:
            f[k] = [kw[k]]
    if "track" in kw:
        f["tracknumber"] = [str(kw["track"])]
    if "disc" in kw:
        f["discnumber"] = [str(kw["disc"])]
    if "lyrics" in kw:
        f["lyrics"] = [kw["lyrics"]]
    if "replaygain" in kw:
        f["replaygain_track_gain"] = [kw["replaygain"]]
    if "art" in kw:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/png"
        pic.data = kw["art"]
        f.clear_pictures()
        f.add_picture(pic)
    f.save()


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0c00000030101005c6b3a4e0000000049454e44ae426082"
)


def reset(d):
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)


def main():
    reset(FIX)
    manifest = []

    # 1. Clean, well-tagged baseline album (2 tracks)
    d = os.path.join(FIX, "01_clean_baseline")
    os.makedirs(d)
    for i, (title, f) in enumerate([("Come Together", 220), ("Something", 330)], start=1):
        p = os.path.join(d, f"{i:02d} - {title}.mp3")
        make_mp3(p, freq=f)
        tag_mp3(p, title=title, artist="The Beatles", album="Abbey Road",
                 track=i, date="1969", genre="Rock", art=TINY_PNG,
                 replaygain="-6.50 dB")
    manifest.append(("clean_baseline", d, "well-tagged real-metadata baseline"))

    # 2. Mojibake case: GBK bytes mis-decoded as latin1/cp437-style garble
    d = os.path.join(FIX, "02_mojibake_chinese")
    os.makedirs(d)
    real_title = "七里香"
    real_artist = "周杰伦"
    garbled_title = real_title.encode("gbk").decode("latin1")
    garbled_artist = real_artist.encode("gbk").decode("latin1")
    p = os.path.join(d, "01 - garbled.mp3")
    make_mp3(p, freq=250)
    tag_mp3(p, title=garbled_title, artist=garbled_artist, album="Common Jasmine Orange",
            track=1, date="2004", genre="Mandopop")
    manifest.append(("mojibake_chinese", d, "GBK bytes stored as if latin1 -> garbled text"))

    # 3. Simplified Chinese release (correctly encoded UTF-8)
    d = os.path.join(FIX, "03_chinese_simplified")
    os.makedirs(d)
    p = os.path.join(d, "01 - 七里香.mp3")
    make_mp3(p, freq=260)
    tag_mp3(p, title="七里香", artist="周杰伦", album="七里香", track=1,
            date="2004", genre="Mandopop")
    manifest.append(("chinese_simplified", d, "correctly-encoded simplified Chinese tags"))

    # 4. Traditional Chinese release
    d = os.path.join(FIX, "04_chinese_traditional")
    os.makedirs(d)
    p = os.path.join(d, "01 - 七里香.mp3")
    make_mp3(p, freq=270)
    tag_mp3(p, title="七里香", artist="周杰倫", album="七里香", track=1,
            date="2004", genre="Mandopop")
    manifest.append(("chinese_traditional", d, "correctly-encoded traditional Chinese tags"))

    # 5. Multi-disc set
    d = os.path.join(FIX, "05_multidisc")
    os.makedirs(d)
    for disc in (1, 2):
        for i in range(1, 3):
            p = os.path.join(d, f"disc{disc}-{i:02d}.mp3")
            make_mp3(p, freq=200 + disc * 10 + i)
            tag_mp3(p, title=f"Track {disc}.{i}", artist="Pink Floyd",
                    album="The Wall", disc=f"{disc}/2", track=i, date="1979")
    manifest.append(("multidisc", d, "2-disc album, disc numbers in tags"))

    # 6. Various-artists compilation
    d = os.path.join(FIX, "06_compilation")
    os.makedirs(d)
    artists = ["Artist A", "Artist B", "Artist C"]
    for i, a in enumerate(artists, start=1):
        p = os.path.join(d, f"{i:02d} - {a}.mp3")
        make_mp3(p, freq=300 + i * 5)
        tag_mp3(p, title=f"Compilation Track {i}", artist=a,
                album="Now That's What I Call Music!", track=i, date="2020",
                genre="Pop")
    manifest.append(("compilation", d, "various-artists compilation"))

    # 7. Live album
    d = os.path.join(FIX, "07_live_album")
    os.makedirs(d)
    p = os.path.join(d, "01 - Live Track.mp3")
    make_mp3(p, freq=180)
    tag_mp3(p, title="Money (Live)", artist="Pink Floyd",
            album="Delicate Sound of Thunder (Live)", track=1, date="1988")
    manifest.append(("live_album", d, "live release naming"))

    # 8. Loose untagged files
    d = os.path.join(FIX, "08_untagged_loose")
    os.makedirs(d)
    for i in range(1, 3):
        p = os.path.join(d, f"track{i}.mp3")
        make_mp3(p, freq=400 + i * 10)
    manifest.append(("untagged_loose", d, "no tags at all, rely on filename"))

    # 9. FLAC (stand-in note: real Monkey's Audio encoder unavailable on this
    # machine; APE coverage is a known gap, flagged in the report)
    d = os.path.join(FIX, "09_flac_format")
    os.makedirs(d)
    p = os.path.join(d, "01 - flac-track.flac")
    make_flac(p, freq=350)
    tag_flac(p, title="Flac Track", artist="Test Artist", album="Format Test",
             track=1, date="2015", genre="Electronic", art=TINY_PNG,
             replaygain="-4.20 dB", lyrics="la la la\nflac lyrics line 2")
    manifest.append(("flac_format", d, "FLAC format tag-preservation fixture"))

    # 10. Album with cover.jpg + .lrc + .cue side files
    d = os.path.join(FIX, "10_side_files")
    os.makedirs(d)
    p = os.path.join(d, "01 - Side Files Track.mp3")
    make_mp3(p, freq=310, seconds=3)
    tag_mp3(p, title="Side Files Track", artist="Test Artist",
            album="Side Files Album", track=1, date="2018")
    with open(os.path.join(d, "cover.jpg"), "wb") as fh:
        fh.write(TINY_PNG)  # PNG bytes are fine as a placeholder cover image
    with open(os.path.join(d, "01 - Side Files Track.lrc"), "w") as fh:
        fh.write("[00:00.00]line one\n[00:05.00]line two\n")
    with open(os.path.join(d, "album.cue"), "w") as fh:
        fh.write(
            'PERFORMER "Test Artist"\n'
            'TITLE "Side Files Album"\n'
            'FILE "01 - Side Files Track.mp3" MP3\n'
            "  TRACK 01 AUDIO\n"
            '    TITLE "Side Files Track"\n'
            "    INDEX 01 00:00:00\n"
        )
    manifest.append(("side_files", d, "cover.jpg + .lrc + .cue companions"))

    # Write manifest
    with open(os.path.join(FIX, "MANIFEST.txt"), "w") as fh:
        for name, path, desc in manifest:
            fh.write(f"{name}\t{os.path.relpath(path, FIX)}\t{desc}\n")

    n_files = sum(len([f for f in files if not f.startswith('.')])
                  for _, _, files in os.walk(FIX))
    print(f"Generated {len(manifest)} fixture groups, {n_files} files total under {FIX}")
    print("NOTE: no real Monkey's Audio (.ape) encoder is available on this Mac "
          "(ffmpeg only decodes APE, brew has no monkeysaudio formula) — APE "
          "tag-preservation (M0b item 4/5) could not be exercised with a real "
          "encoder. Flagged as an open gap in the evidence report.")


if __name__ == "__main__":
    main()
