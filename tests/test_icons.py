"""The app icon (tools/make_icons.py): every icon the page links to exists, at the right size."""
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "music_steward/web"


def png_size(p: Path) -> tuple[int, int]:
    head = p.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", p
    return struct.unpack(">II", head[16:24])


def test_the_page_links_icons_that_exist():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'<link rel="(?:icon|apple-touch-icon)"[^>]*href="/static/([^"]+)"', html)
    assert len(hrefs) == 3
    for h in hrefs:
        assert (WEB / h).is_file(), h


def test_icon_sizes_and_masters():
    assert png_size(WEB / "icons/favicon-32.png") == (32, 32)
    assert png_size(WEB / "icons/apple-touch-icon.png") == (180, 180)
    assert png_size(WEB / "icons/icon-512.png") == (512, 512)
    assert png_size(ROOT / "assets/icon/ios/AppIcon-1024.png") == (1024, 1024)
    for svg in ("assets/icon/music-steward.svg", "assets/icon/music-steward-macos.svg", "music_steward/web/icons/favicon.svg"):
        text = (ROOT / svg).read_text(encoding="utf-8")
        assert text.startswith("<svg") and 'viewBox="0 0 1024 1024"' in text and "mask" in text
    assert (ROOT / "assets/icon/MusicSteward.icns").read_bytes()[:4] == b"icns"


def test_icons_are_packaged():
    assert '"web/icons/*"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
