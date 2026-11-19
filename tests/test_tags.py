import pytest

from music_steward.tags import repair_latin1


@pytest.mark.parametrize("real", ["夜航", "郑绪岚", "人海中一個你", "器名", "免"])
def test_gbk_mojibake_is_repaired(real):
    shown = real.encode("gb18030").decode("latin-1")
    r = repair_latin1(shown)
    assert r and r["proposal"] == real and r["encoding"] == "gb18030"


def test_big5_mojibake_is_repaired():
    real = "劉德華"
    shown = real.encode("big5").decode("latin-1")
    r = repair_latin1(shown)
    assert r and r["proposal"] == real and "big5" in r["readings"]


@pytest.mark.parametrize("latin", ["Beyoncé", "Café del Mar", "Motörhead", "Sigur Rós",
                                   "Björk Guðmundsdóttir", "Plain ASCII"])
def test_real_latin1_is_left_alone(latin):
    assert repair_latin1(latin) is None


def _apev2(items: dict[str, bytes]) -> bytes:
    import struct
    body = b"".join(struct.pack("<2I", len(v), 0) + k.encode() + b"\x00" + v for k, v in items.items())
    foot = b"APETAGEX" + struct.pack("<4I", 2000, len(body) + 32, len(items), 0) + b"\x00" * 8
    return body + foot


def test_apev2_with_gbk_bytes_is_read_leniently(tmp_path):
    """mutagen refuses an APEv2 tag holding GBK bytes ('utf-8' codec can't decode): the whole
    file used to come back unreadable. Seen on the NAS (孟庭苇 - 真的还是假的)."""
    from music_steward.tags import read_tags
    p = tmp_path / "01.ape"
    p.write_bytes(b"MAC \x96\x0f" + b"\x00" * 200 + _apev2(
        {"Artist": "孟庭苇".encode("gbk"), "Album": "真的还是假的".encode("gbk"), "Year": b"1995", "Title": "Song"
         .encode()}))
    t = read_tags(str(p))
    fixes = {i["field"]: i["proposal"] for i in t.issues if i.get("proposal")}
    assert t.tag_format == "APEv2 (lenient)" and t.values["date"] == "1995" and t.values["title"] == "Song"
    assert fixes == {"artist": "孟庭苇", "album": "真的还是假的"}
    assert any(i["kind"] == "tag_read_fallback" for i in t.issues)


def test_ape_with_id3_prefix_reads_the_id3_block(tmp_path):
    from mutagen.id3 import ID3, TALB, TPE1, TIT2
    from music_steward.tags import read_tags
    p = tmp_path / "天上的风.ape"
    p.write_bytes(b"MAC \x96\x0f" + b"\x00" * 300)
    t = ID3()
    t.add(TIT2(encoding=0, text=["Track01"]))
    t.add(TPE1(encoding=0, text=["德德玛".encode("gbk").decode("latin-1")]))
    t.add(TALB(encoding=0, text=["天上的风".encode("gbk").decode("latin-1")]))
    t.save(str(p))                                   # puts the ID3 block in FRONT of the APE data
    ti = read_tags(str(p))
    fixes = {i["field"]: i["proposal"] for i in ti.issues if i.get("proposal")}
    assert ti.values["title"] == "Track01" and fixes == {"artist": "德德玛", "album": "天上的风"}
