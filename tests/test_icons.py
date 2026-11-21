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


# ---- the Dock icon of the desktop window (Codex review on #8) ---------------------------------------
import sys
import types

import pytest


def _fake_appkit(monkeypatch, image_ok=True, boom=False):
    """A stand-in for AppKit: records what the helper does, works on any platform."""
    calls = {"set": [], "loaded": []}

    class Image:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithContentsOfFile_(self, path):
            calls["loaded"].append(path)
            return self if image_ok else None

    class App:
        def setApplicationIconImage_(self, image):
            if boom:
                raise RuntimeError("AppKit exploded")
            calls["set"].append(image)

    app = App()
    mod = types.ModuleType("AppKit")
    mod.NSImage = Image
    mod.NSApplication = types.SimpleNamespace(sharedApplication=lambda: app)
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    return calls


def test_dock_icon_is_installed_on_macos(monkeypatch):
    from music_steward import desktop
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = _fake_appkit(monkeypatch)
    desktop._dock_icon()
    assert calls["loaded"] == [str(desktop.ICON)] and len(calls["set"]) == 1
    assert desktop.ICON.is_file()                       # the icon the helper loads is the shipped one


def test_dock_icon_is_skipped_off_macos_and_without_the_file(monkeypatch, tmp_path):
    from music_steward import desktop
    calls = _fake_appkit(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    desktop._dock_icon()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "ICON", tmp_path / "missing.png")
    desktop._dock_icon()
    assert calls == {"set": [], "loaded": []}


@pytest.mark.parametrize("kind", ["unreadable image", "AppKit error", "AppKit missing"])
def test_dock_icon_failures_never_stop_the_window(monkeypatch, kind):
    from music_steward import desktop
    monkeypatch.setattr(sys, "platform", "darwin")
    if kind == "AppKit missing":
        monkeypatch.setitem(sys.modules, "AppKit", None)           # import raises ImportError
    else:
        _fake_appkit(monkeypatch, image_ok=kind != "unreadable image", boom=kind == "AppKit error")
    desktop._dock_icon()                                           # must not raise
