"""Color constants — Excalidraw-style pastels on warm paper."""
from __future__ import annotations

INK = "#1e1e1e"
PAPER = "#fbf9f4"

# Ordered by prominence; scenes cycle through these for accents.
PALETTE: list[str] = [
    "#ff6b4a",  # coral
    "#2d7dd2",  # blue
    "#f4b942",  # amber
    "#3fbf7f",  # green
    "#a06cd5",  # purple
    "#e85d8a",  # pink
]


def accent(i: int, override: list[str] | None = None) -> str:
    pal = override or PALETTE
    return pal[i % len(pal)]
