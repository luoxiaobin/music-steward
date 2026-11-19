"""Audio hash must ignore tag edits (per format) and catch audio edits."""
import shutil

import pytest

from mp3librarytool.hashing import audio_hash, audio_hash_method

from .conftest import APE_SAMPLE, needs_ffmpeg, tone

pytestmark = needs_ffmpeg


def _edit_tags(p):
    import mutagen
    from mutagen.apev2 import APEv2
    from mutagen.id3 import APIC, ID3, TIT2
    s = str(p)
    if s.endswith(".mp3"):
        t = ID3()
        t.add(TIT2(encoding=3, text=["x" * 300]))
        t.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=b"\x89PNG" + b"0" * 4000))
        t.save(p, v1=2)                       # ID3v2 + ID3v1
        a = APEv2()
        a["Title"] = "ape tag on mp3"
        a.save(p)                             # plus a trailing APEv2 tag
    elif s.endswith(".ape"):
        a = APEv2()
        a["Title"] = "t" * 300
        a.save(p)
    else:
        f = mutagen.File(p)
        if s.endswith(".wav"):
            f.add_tags()
            f.tags.add(TIT2(encoding=3, text=["w"]))
        elif s.endswith(".m4a"):
            f["\xa9nam"] = ["z" * 300]
        else:
            f["TITLE"] = ["y" * 300]
        f.save()


@pytest.mark.parametrize("ext,args", [
    (".mp3", ["-c:a", "libmp3lame"]), (".flac", ["-c:a", "flac"]),
    (".m4a", ["-c:a", "aac"]), (".wav", ["-c:a", "pcm_s16le"])])
def test_audio_hash_ignores_tags(tmp_path, ext, args):
    p = tone(tmp_path / f"a{ext}", args)
    h0, method = audio_hash(str(p), ext)
    assert method == "packet"
    _edit_tags(p)
    assert audio_hash(str(p), ext)[0] == h0
    q = tone(tmp_path / f"b{ext}", args, freq=441)
    assert audio_hash(str(q), ext)[0] != h0


@pytest.mark.skipif(not APE_SAMPLE, reason="set MLT_APE_SAMPLE to an .ape file to run")
def test_ape_uses_pcm_hash_and_ignores_tags(tmp_path):
    p = tmp_path / "a.ape"
    shutil.copy(APE_SAMPLE, p)
    assert audio_hash_method(".ape") == "pcm"
    h0, _ = audio_hash(str(p), ".ape")
    _edit_tags(p)
    assert audio_hash(str(p), ".ape")[0] == h0
