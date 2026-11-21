"""Interface languages: table consistency and Traditional Chinese generation."""
import re

from music_steward import i18n


def test_table_is_consistent():
    keys = [r[0] for r in i18n.TABLE]
    assert len(keys) == len(set(keys)), "duplicate English entries"
    for row in i18n.TABLE:
        assert len(row) == 4 and all(isinstance(x, str) and x for x in row), row
        slots = sorted(re.findall(r"\{\w+\}", row[0]))
        for tr in row[1:]:
            assert sorted(re.findall(r"\{\w+\}", tr)) == slots, row


def test_languages_and_traditional_chinese():
    assert set(i18n.LANGUAGES) == {"en", "zh-Hans", "zh-Hant", "fr-CA", "es-US"}
    assert i18n.dictionary("en") == {}
    hant = i18n.dictionary("zh-Hant")
    assert hant["Folders"] == "資料夾" and hant["Settings"] == "設定" and hant["Search"] == "搜尋"
    assert hant["Albums · {n} recognized units in {where}"].count("{where}") == 1   # slots untouched
    assert i18n.dictionary("fr-CA")["Settings"] == "Paramètres" and i18n.dictionary("es-US")["Settings"] == "Configuración"


def test_the_retire_screens_mark_album_names_and_paths_as_data():
    """P1 (Codex, round 6): an album titled "Album" was shown translated in the retire dialog. Everything the
    user's collection supplies must sit in an element the page translator skips."""
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "music_steward/web"
    js = (web / "plan.js").read_text(encoding="utf-8")
    assert 'data-no-translate' in re.search(r"const reasonLi = .*?\n\n", js, re.S).group(0)
    for needle in ("b.folder)", "r.folder)", "r.blocked.slice", "x.label)", "(x.folder"):
        lines = [ln for ln in js.splitlines() if needle in ln]
        assert lines and all("data-no-translate" in ln for ln in lines), needle
    assert "data-no-translate" in next(ln for ln in (web / "app.js").read_text(encoding="utf-8").splitlines()
                                       if '"Allow for "' in ln)


def test_the_folder_check_shows_paths_as_data():
    """What the user typed (and the path offered back) must never be run through the interface translator."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "music_steward/web/app.js").read_text(encoding="utf-8")
    start = js.index("function folderCheck(")
    body = js[start:js.index("function chooseButton(")]
    for needle in ("r.detail", "r.suggestion"):
        assert all("data-no-translate" in ln for ln in body.splitlines() if needle in ln and "h(" in ln), needle
