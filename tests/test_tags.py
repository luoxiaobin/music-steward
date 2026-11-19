import pytest

from mp3librarytool.tags import repair_latin1


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
