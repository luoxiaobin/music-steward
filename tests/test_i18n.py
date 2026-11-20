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
