"""Font loading. Prefers a hand-drawn face (Comic Sans / Segoe Print on Windows,
DejaVu in the Docker image), falls back to PIL's bundled default."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# Searched in order; first hit wins. Hand-drawn faces first.
_CANDIDATES = [
    r"C:\Windows\Fonts\segoepr.ttf",   # Segoe Print
    r"C:\Windows\Fonts\comic.ttf",     # Comic Sans MS
    r"C:\Windows\Fonts\comicbd.ttf",   # Comic Sans MS Bold
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    for cand in _CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


@lru_cache(maxsize=64)
def get_font(size: int) -> ImageFont.FreeTypeFont:
    path = _font_path()
    if path is not None:
        try:
            return ImageFont.truetype(path, size)
        except OSError:  # pragma: no cover
            pass
    # PIL >= 10 accepts a size for the bundled bitmap font.
    try:
        return ImageFont.load_default(size)
    except TypeError:  # pragma: no cover - very old PIL
        return ImageFont.load_default()
