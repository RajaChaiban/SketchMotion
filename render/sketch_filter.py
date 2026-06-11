"""Per-frame sketch stylization — turns real photographic frames into hand-drawn looks.

Pure pixel processing (PIL + numpy, no scipy so it works without the dev deps). Two styles:
- ``ink``    : bold ink outlines over posterized, paper-tinted color (cartoon/comic).
- ``pencil`` : grayscale color-dodge pencil sketch on paper (graphite).

`sketchify(img, style)` is stateless and deterministic. Hot paths are tuned: edge-thickening
uses a numpy 3x3 dilation (PIL's MaxFilter was ~85ms/frame), and the color blend runs in-place
in 0..255 space (no /255 round-trips).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

PAPER_RGB = (251, 249, 244)
STYLES = ("ink", "pencil")


def _dilate3(a: np.ndarray, times: int) -> np.ndarray:
    """Fast 3x3 grayscale dilation (max filter) in numpy — replaces PIL MaxFilter."""
    h, w = a.shape
    for _ in range(max(0, times)):
        p = np.pad(a, 1, mode="edge")
        out = a.copy()
        for dy in range(3):
            for dx in range(3):
                np.maximum(out, p[dy:dy + h, dx:dx + w], out=out)
        a = out
    return a


def _ink(img: Image.Image, posterize_bits: int, edge_strength: float,
         thicken: int, color_mix: float) -> Image.Image:
    gray = img.convert("L")
    e = np.array(gray.filter(ImageFilter.FIND_EDGES), dtype=np.float32)  # writable copy
    e *= edge_strength / 255.0
    if thicken > 0:
        e = _dilate3(e, thicken)
    np.clip(e, 0.0, 1.0, out=e)
    keep = 1.0 - e                                          # 1 where we keep color, 0 on ink

    poster = ImageOps.posterize(img, max(1, posterize_bits))
    col = np.array(poster, dtype=np.float32)               # 0..255, writable
    paper = np.asarray(PAPER_RGB, dtype=np.float32)
    col *= color_mix                                       # soften flats toward paper
    col += paper * (1.0 - color_mix)
    col *= keep[..., None]                                 # lay dark ink lines on top
    np.clip(col, 0.0, 255.0, out=col)
    return Image.fromarray(col.astype(np.uint8), "RGB")


def _pencil(img: Image.Image, blur_radius: float) -> Image.Image:
    gray = img.convert("L")
    inv = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(blur_radius))
    g = np.array(gray, dtype=np.float32)
    b = np.array(inv, dtype=np.float32)
    denom = 255.0 - b
    np.maximum(denom, 1.0, out=denom)
    dodge = g * (255.0 / denom)                            # color-dodge, 0..255
    np.clip(dodge, 0.0, 255.0, out=dodge)
    paper = np.asarray(PAPER_RGB, dtype=np.float32) / 255.0
    out = dodge[..., None] * paper[None, None, :]          # graphite where dark, paper where light
    return Image.fromarray(out.astype(np.uint8), "RGB")


def sketchify(
    img: Image.Image,
    style: str = "ink",
    *,
    posterize_bits: int = 3,
    edge_strength: float = 1.6,
    thicken: int = 1,
    color_mix: float = 0.7,
    blur_radius: float = 12.0,
) -> Image.Image:
    """Stylize one frame. Returns a same-size RGB image."""
    img = img.convert("RGB")
    if style == "pencil":
        return _pencil(img, blur_radius)
    if style == "ink":
        return _ink(img, posterize_bits, edge_strength, thicken, color_mix)
    raise ValueError(f"unknown sketch style {style!r}; choose from {STYLES}")
