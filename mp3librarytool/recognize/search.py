"""User-supplied search for "Search again": terms, or a pasted MusicBrainz link / id."""
from __future__ import annotations

import re

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LINK = re.compile(rf"musicbrainz\.org/(release|recording)/({UUID})", re.I)


class SearchError(ValueError):
    pass


def parse(kind: str, artist: str = "", name: str = "", link: str = "") -> dict:
    """kind: 'album' or 'track'. Returns the search dict the adapter understands."""
    link = (link or "").strip()
    if link:
        m = LINK.search(link)
        if m:
            what, mbid = m.group(1).lower(), m.group(2).lower()
            if kind == "album" and what != "release":
                raise SearchError("for an album, paste a MusicBrainz *release* link")
            if kind == "track" and what != "recording":
                raise SearchError("for a single track, paste a MusicBrainz *recording* link")
            return {"ids": [mbid], "link": link}
        if re.fullmatch(UUID, link, re.I):
            return {"ids": [link.lower()], "link": link}
        raise SearchError("not a MusicBrainz link or id")
    artist, name = (artist or "").strip(), (name or "").strip()
    if not artist or not name:
        raise SearchError("enter both the artist and the " + ("album" if kind == "album" else "title"))
    return {"artist": artist, ("album" if kind == "album" else "title"): name}
