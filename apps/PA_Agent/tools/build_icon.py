"""Render PA Agent SVG icons to multi-size PNGs and pack assets/pa-agent.ico.

Frames at 16/24/32px use the hinted small artwork (pa-agent-icon-small.svg,
scheme C: body/wick 40/16); 48px and above are rendered from the 256px
master (pa-agent-icon.svg).

Usage:
    python tools/build_icon.py

Outputs:
    assets/icon-png/pa-agent-{16,24,32,48,64,128,256}.png  (per-size renders)
    assets/pa-agent.ico                                     (multi-size icon)
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
MAIN_SVG = os.path.join(ASSETS, "pa-agent-icon.svg")
SMALL_SVG = os.path.join(ASSETS, "pa-agent-icon-small.svg")
PNG_DIR = os.path.join(ASSETS, "icon-png")
ICO_OUT = os.path.join(ASSETS, "pa-agent.ico")

# Sizes rendered from each artwork.
MAIN_SIZES = (48, 64, 128, 256)
SMALL_SIZES = (16, 24, 32)


def render_svg(svg_path: str, size: int) -> QImage:
    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {svg_path}")
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - needed for QImage rendering
    os.makedirs(PNG_DIR, exist_ok=True)

    renders: dict[int, str] = {}
    for size in SMALL_SIZES:
        renders[size] = SMALL_SVG
    for size in MAIN_SIZES:
        renders[size] = MAIN_SVG

    png_paths: dict[int, str] = {}
    for size in sorted(renders):
        img = render_svg(renders[size], size)
        out = os.path.join(PNG_DIR, f"pa-agent-{size}.png")
        if not img.save(out):
            raise RuntimeError(f"Failed to save {out}")
        png_paths[size] = out
        src = "small" if renders[size] == SMALL_SVG else "main"
        print(f"  rendered {size:>3}px  <- {src}  -> {out}")

    from PIL import Image

    sizes = sorted(png_paths)
    images = {s: Image.open(png_paths[s]) for s in sizes}
    base = images[sizes[-1]]
    base.save(
        ICO_OUT,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=[images[s] for s in sizes[:-1]],
    )
    print(f"packed {len(sizes)} sizes {sizes} -> {ICO_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
