#!/usr/bin/env python3
"""Build the site's icons from one square source image.

Run when the source changes; the outputs are committed, so a normal build and
deploy need nothing from here and Pillow stays out of the runtime.

The badge is a navy disc on a white square. Cutting the square away matters:
left in, a favicon shows as a white tile on any dark browser chrome. The
surround is removed by flood-filling inward from the corners rather than by
masking a guessed circle -- most of the artwork inside the disc is also white,
and a circle mask that is a few pixels out clips the dome.

    .venv/bin/python scripts/make_icons.py .working/florida-session-watch.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "flba" / "static"

# Wide enough to cross the antialiased rim of the disc, narrow enough to stop
# at the navy. Measured: surround is 250-254, the disc is 7,36,71.
TOLERANCE = 60
SENTINEL = (255, 0, 255, 0)


def cut_surround(im: Image.Image) -> Image.Image:
    """Everything reachable from a corner without crossing the disc goes."""
    im = im.convert("RGBA")
    w, h = im.size
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(im, xy, SENTINEL, thresh=TOLERANCE)
    px = im.load()
    for y in range(h):
        for x in range(w):
            if px[x, y][:3] == SENTINEL[:3]:
                px[x, y] = (0, 0, 0, 0)
    im = im.crop(im.getbbox())
    # The disc's bounding box is not exactly square -- 984x998 on the source --
    # and resizing that straight to 192x192 stretches it by a percent and a
    # half. Pad to a square first so the circle stays a circle.
    side = max(im.size)
    pad = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    pad.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2))
    return pad


def square(im: Image.Image, size: int, bg=None) -> Image.Image:
    """One icon. `bg` fills the corners for platforms that mask their own."""
    art = im.resize((size, size), Image.LANCZOS)
    if bg is None:
        return art
    tile = Image.new("RGBA", (size, size), bg)
    tile.alpha_composite(art)
    return tile.convert("RGB")


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1
               else ROOT / ".working" / "florida-session-watch.png")
    if not src.exists():
        print(f"no source image at {src}", file=sys.stderr)
        return 2
    badge = cut_surround(Image.open(src))
    print(f"{src.name}: cropped to {badge.size[0]}x{badge.size[1]}")

    # The navy the disc is drawn in, taken from the art rather than typed in.
    navy = badge.convert("RGB").getpixel((badge.size[0] // 2, 4))

    written = []
    # One .ico carrying the three sizes a browser actually asks for.
    ico = OUT / "favicon.ico"
    badge.save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    written.append(ico)

    for name, size, bg in (("icon-192.png", 192, None),
                           ("icon-512.png", 512, None),
                           ("apple-touch-icon.png", 180, navy)):
        path = OUT / name
        square(badge, size, bg).save(path, optimize=True)
        written.append(path)

    for p in written:
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
