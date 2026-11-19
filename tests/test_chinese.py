import pytest

from music_steward import chinese
from music_steward.recognize import results


@pytest.mark.parametrize("trad,simp", [("經典老歌", "经典老歌"), ("喀什噶爾胡楊", "喀什噶尔胡杨"), ("劉德華", "刘德华")])
def test_both_scripts_share_one_key(trad, simp):
    assert chinese.key(trad) == chinese.key(simp) == simp


def test_other_script():
    assert chinese.other_script("郑绪岚 情系红楼梦") == "鄭緒嵐 情繫紅樓夢"
    assert chinese.other_script("喀什噶爾胡楊") == "喀什噶尔胡杨"
    assert chinese.other_script("Phil Collins") is None and chinese.other_script(None) is None


def test_title_check_ignores_script():
    assert results.title_agrees("经典老歌101 CD2", "經典老歌")
    assert results.title_agrees("喀什噶尔胡杨", "喀什噶爾胡楊")
    assert not results.title_agrees("纯美之声", "穿越的聲音")


def test_merge_keeps_each_release_once_with_lowest_distance():
    from music_steward.recognize.beets_adapter import _merge

    class D:
        def __init__(self, d): self.distance = d

    class I:
        def __init__(self, src, ident): self.data_source, self.identifier = src, ident

    class C:
        def __init__(self, src, ident, d): self.info, self.distance = I(src, ident), D(d)
    out = _merge([C("MusicBrainz", "a", 0.4), C("Discogs", "b", 0.5)], [C("MusicBrainz", "a", 0.2), C("Deezer", "c", 0.3)])
    assert [(c.info.identifier, c.distance.distance) for c in out] == [("a", 0.2), ("c", 0.3), ("b", 0.5)]
