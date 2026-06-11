"""Per-frame sketch stylization — turns real photographic frames into hand-drawn looks.

Pure pixel processing (PIL + numpy), no LLM. Two styles:
- ``ink``    : bold ink outlines over posterized, paper-tinted color (cartoon/comic).
- ``pencil`` : grayscale color-dodge pencil sketch on paper (graphite).

`sketchify(img, style)` is stateless and deterministic, so it parallelizes cleanly across
frames and is trivially testable.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

PAPER_RGB = (251, 249, 244)
STYLES = ("ink", "pencil")


def _edge_mask(gray: Image.Image, strength: float, thicken: int) -> np.ndarray:
    """1.0 where the line is (dark ink), 0..1 elsewhere — ready to multiply over color."""
    edges = gray.filter(ImageFilter.FIND_EDGES)
    for _ in range(max(0, thicken)):
        edges = edges.filter(ImageFilter.MaxFilter(3))
    e = np.asarray(edges, dtype=np.float32) / 255.0
    return np.clip(e * strength, 0.0, 1.0)


def _ink(img: Image.Image, posterize_bits: int, edge_strength: float,
         thicken: int, color_mix: float) -> Image.Image:
    gray = img.convert("L")
    e = _edge_mask(gray, edge_strength, thicken)          # ink presence 0..1
    keep = 1.0 - e                                         # 1 where we keep color, 0 on lines

    poster = ImageOps.posterize(img, max(1, posterize_bits))
    p = np.asarray(poster, dtype=np.float32) / 255.0
    paper = np.asarray(PAPER_RGB, dtype=np.float32) / 255.0
    # soften the flats toward paper so it reads as drawn, not photographic
    col = p * color_mix + paper * (1.0 - color_mix)
    out = col * keep[..., None]                            # lay dark ink lines on top
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), "RGB")


def _pencil(img: Image.Image, blur_radius: float) -> Image.Image:
    gray = img.convert("L")
    g = np.asarray(gray, dtype=np.float32)
    inv = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(blur_radius))
    b = np.asarray(inv, dtype=np.float32)
    # color-dodge: gray * 255 / (255 - blurred_inverse)
    dodge = np.clip(g * 255.0 / (255.0 - b + 1e-3), 0, 255) / 255.0
    paper = np.asarray(PAPER_RGB, dtype=np.float32) / 255.0
    out = paper[None, None, :] * dodge[..., None]          # graphite where dark, paper where light
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), "RGB")


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
