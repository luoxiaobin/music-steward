"""Music Steward app icon: one geometry -> the SVG masters and every platform size.

The chosen concept (owner, 2026-11): an ivory folder with a music note in negative space, on teal.
The shapes are defined once here, on a 1024 grid; the script writes them as SVG (vector masters)
and draws them with Pillow (4x supersampled, then downscaled) for the PNG sizes, so the SVG and the
PNGs can never drift apart.

    pip install -e '.[icons]' && python tools/make_icons.py

Outputs
  assets/icon/music-steward.svg          full-bleed square master (iOS / web; the platform rounds it)
  assets/icon/music-steward-macos.svg    macOS master: rounded body on the 824/1024 grid, transparent margin
  assets/icon/MusicSteward.icns          macOS app icon (16 ... 1024, via iconutil; macOS only)
  assets/icon/ios/AppIcon-1024.png       iOS single-size app icon (opaque, full bleed) + Contents.json
  music_steward/web/icons/               served by the app: favicon.svg, favicon-32.png,
                                         apple-touch-icon.png (180), icon-512.png (Dock icon)
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/icon"
WEB = ROOT / "music_steward/web/icons"

TEAL_TOP, TEAL_BOTTOM = (20, 138, 124), (12, 104, 95)
IVORY = (245, 235, 214)

# ---- geometry on a 1024 x 1024 full-bleed square --------------------------------------------
FOLDER_BODY = (196, 336, 828, 772, 52)                 # x0, y0, x1, y1, corner radius
TAB_RECT = (196, 276, 430, 480, 40)                      # the tab: a rounded block behind the body's top ...
TAB_SLOPE = [(380, 276), (424, 276), (496, 352), (380, 352)]   # ... with a slanted right edge
NOTE_DX, NOTE_DY = -45, 12                                # optical centring inside the folder


def _ellipse(cx, cy, rx, ry, angle_deg, n=48):
    a = math.radians(angle_deg)
    return [(cx + rx * math.cos(t) * math.cos(a) - ry * math.sin(t) * math.sin(a),
             cy + rx * math.cos(t) * math.sin(a) + ry * math.sin(t) * math.cos(a))
            for t in (2 * math.pi * i / n for i in range(n))]


def note_shapes():
    """Two beamed eighth notes as polygons (heads tilted like engraved note heads)."""
    d = lambda pts: [(x + NOTE_DX, y + NOTE_DY) for x, y in pts]          # noqa: E731
    head1 = _ellipse(440, 640, 74, 54, -22)
    head2 = _ellipse(668, 598, 74, 54, -22)
    # stems end at the middle of their heads, entirely inside them: no notch, no corner sticking out
    stem1 = [(472, 640), (506, 634), (506, 452), (472, 458)]
    stem2 = [(700, 598), (734, 592), (734, 410), (700, 416)]
    beam = [(472, 446), (734, 398), (734, 474), (472, 522)]
    return [d(head1), d(head2), d(stem1), d(stem2), d(beam)]


# ---- SVG ------------------------------------------------------------------------------------
def _path(pts):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"


def svg(macos: bool = False) -> str:
    """Full-bleed square, or the macOS layout (body scaled to 824 and centred, rounded, margin)."""
    x0, y0, x1, y1, r = FOLDER_BODY
    notes = "".join(f'<path d="{_path(p)}"/>' for p in note_shapes())
    iv = f"#{IVORY[0]:02x}{IVORY[1]:02x}{IVORY[2]:02x}"
    tx0, ty0, tx1, ty1, tr = TAB_RECT
    art = (f'<g mask="url(#cut)" fill="{iv}"><rect x="{tx0}" y="{ty0}" width="{tx1 - tx0}" height="{ty1 - ty0}" rx="{tr}"/>'
           f'<path d="{_path(TAB_SLOPE)}"/>'
           f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}"/></g>')
    grad = (f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="rgb{TEAL_TOP}"/>'
            f'<stop offset="1" stop-color="rgb{TEAL_BOTTOM}"/></linearGradient>'
            f'<mask id="cut" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
            f'<rect width="1024" height="1024" fill="#fff"/><g fill="#000">{notes}</g></mask>')
    if macos:
        body = f'<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>'
        inner = f'<g transform="translate(100 100) scale({824 / 1024})">{art}</g>'
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">'
                f'<defs>{grad}</defs>{body}{inner}</svg>\n')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">'
            f'<defs>{grad}</defs><rect width="1024" height="1024" fill="url(#bg)"/>{art}</svg>\n')


# ---- raster ---------------------------------------------------------------------------------
def _gradient(size):
    img = Image.new("RGBA", (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(TEAL_TOP, TEAL_BOTTOM)) + (255,)
        for x in range(size):
            px[x, y] = c
    return img


def render(size: int, macos: bool = False, rounded: float | None = None) -> Image.Image:
    """The icon at `size` px. macOS: the 824 body with margin; rounded: corner radius as a fraction
    of the side for a full-bleed rounded square (web favicons); else an opaque full-bleed square."""
    S = 4 * 1024                                   # supersampled canvas
    k = S / 1024
    bg = _gradient(S)
    folder = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(folder)
    x0, y0, x1, y1, r = FOLDER_BODY
    tx0, ty0, tx1, ty1, tr = TAB_RECT
    d.rounded_rectangle([tx0 * k, ty0 * k, tx1 * k, ty1 * k], radius=tr * k, fill=IVORY + (255,))
    d.polygon([(x * k, y * k) for x, y in TAB_SLOPE], fill=IVORY + (255,))
    d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=r * k, fill=IVORY + (255,))
    for p in note_shapes():                        # negative space: cut the notes out of the folder
        d.polygon([(x * k, y * k) for x, y in p], fill=(0, 0, 0, 0))
    art = Image.alpha_composite(bg, folder)
    if macos:
        inner = art.resize((round(824 * k), round(824 * k)), Image.LANCZOS)
        mask = Image.new("L", inner.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, inner.size[0] - 1, inner.size[1] - 1], radius=185 * k, fill=255)
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        out.paste(inner, (round(100 * k), round(100 * k)), mask)
        art = out
    elif rounded:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=rounded * S, fill=255)
        art.putalpha(mask)
    return art.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    (OUT / "music-steward.svg").write_text(svg(), encoding="utf-8")
    (OUT / "music-steward-macos.svg").write_text(svg(macos=True), encoding="utf-8")
    # iOS: one 1024 opaque full-bleed image (Xcode 14+ single-size app icon; iOS applies the mask)
    ios = OUT / "ios"
    ios.mkdir(exist_ok=True)
    render(1024).convert("RGB").save(ios / "AppIcon-1024.png")
    (ios / "Contents.json").write_text(json.dumps({
        "images": [{"filename": "AppIcon-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}],
        "info": {"author": "xcode", "version": 1}}, indent=2) + "\n", encoding="utf-8")
    # served by the app
    (WEB / "favicon.svg").write_text(svg().replace('<rect width="1024" height="1024" fill="url(#bg)"/>',
                                                   '<rect width="1024" height="1024" rx="224" fill="url(#bg)"/>'),
                                     encoding="utf-8")
    render(32, rounded=0.22).save(WEB / "favicon-32.png")
    render(180).convert("RGB").save(WEB / "apple-touch-icon.png")      # iPhone home screen (opaque)
    render(512, macos=True).save(WEB / "icon-512.png")                  # Dock icon of the desktop window
    # macOS .icns
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as t:
            iconset = Path(t) / "MusicSteward.iconset"
            iconset.mkdir()
            for pt in (16, 32, 128, 256, 512):
                render(pt, macos=True).save(iconset / f"icon_{pt}x{pt}.png")
                render(pt * 2, macos=True).save(iconset / f"icon_{pt}x{pt}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "MusicSteward.icns")], check=True)
    else:
        print("iconutil not found (macOS only): MusicSteward.icns not rebuilt", file=sys.stderr)
    # small-size proofs, to look at (not shipped)
    proof = Image.new("RGBA", (16 + 32 + 64 + 128 + 50, 140), (250, 245, 235, 255))
    x = 10
    for s in (16, 32, 64, 128):
        proof.paste(img := render(s, macos=True), (x, 130 - s), img)
        x += s + 10
    proof.save(Path(tempfile.gettempdir()) / "music-steward-icon-proof.png")     # to look at; not kept
    print("written:", *sorted(str(p.relative_to(ROOT)) for p in list(OUT.rglob("*")) + list(WEB.glob("*")) if p.is_file()),
          sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
