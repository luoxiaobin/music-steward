"""Simplified / Traditional Chinese helpers (OpenCC, Apache-2.0, pure-Python build).

Used for SEARCH and COMPARISON keys only -- tags are never rewritten by this module.
(beets' own scoring already compares Chinese via pinyin transliteration, so 刘 and 劉 score
alike; the literal-text services we search -- MusicBrainz, Discogs, Deezer -- do not.)
"""
from __future__ import annotations

import re
from functools import lru_cache

_CJK = re.compile(r"[㐀-鿿]")


@lru_cache(maxsize=1)
def _cc():
    from opencc import OpenCC
    return OpenCC("t2s"), OpenCC("s2t")


def has_cjk(s: str | None) -> bool:
    return bool(s and _CJK.search(s))


@lru_cache(maxsize=65536)
def to_simplified(s: str) -> str:
    return _cc()[0].convert(s) if has_cjk(s) else s


@lru_cache(maxsize=65536)
def to_traditional(s: str) -> str:
    return _cc()[1].convert(s) if has_cjk(s) else s


def key(s: str | None) -> str:
    """Comparison key: Simplified form, so 經典老歌 and 经典老歌 compare equal."""
    return to_simplified(s) if s else ""


def other_script(s: str | None) -> str | None:
    """The same text in the other script, or None if converting changes nothing."""
    if not has_cjk(s):
        return None
    t = to_traditional(s)
    if t != s:
        return t
    simp = to_simplified(s)
    return simp if simp != s else None
