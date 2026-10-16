#!/usr/bin/env python3
"""Convert a ship PNG/WebP into multi-size assets/app-icon.ico (16/32/48/256)."""
from __future__ import annotations
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("pip install pillow", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "app-icon.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]

def main(src: str) -> None:
    src_p = Path(src)
    if not src_p.is_file():
        raise SystemExit(f"missing source: {src_p}")
    im = Image.open(src_p).convert("RGBA")
    # Center-crop to square if needed
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Pillow saves multi-size ICO via sizes=
    im.save(OUT, format="ICO", sizes=SIZES)
    print(f"wrote {OUT} from {src_p} sizes={SIZES}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_app_ico.py <ship.png>")
    main(sys.argv[1])
