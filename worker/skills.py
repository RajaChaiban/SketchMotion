"""Skill loader — each pipeline has a skill (markdown rules + structured presets).

A skill is `skills/<name>/SKILL.md`. The text is injected where the agent makes decisions
(e.g. the spec-compile prompt for `prompt-refiner`); the structured presets here back the
`video-stylist` skill so style choices are code-usable and testable.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=16)
def load_skill(name: str) -> str:
    """Return a skill's SKILL.md text, or '' if it doesn't exist (passthrough)."""
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# --- video-stylist structured presets ---------------------------------------

STYLE_PRESETS: dict[str, dict] = {
    "ink": {"posterize_bits": 3, "edge_strength": 1.6, "thicken": 1, "color_mix": 0.7},
    "pencil": {"blur_radius": 12.0},
}

# Content hints -> preferred style (used by the auto chooser).
_HINT_TO_STYLE = {
    "sports": "ink", "action": "ink", "crowd": "ink", "product": "ink",
    "portrait": "pencil", "face": "pencil", "calm": "pencil",
}


def style_opts(style: str) -> dict:
    """Filter parameters for a concrete style."""
    return dict(STYLE_PRESETS.get(style, {}))


def choose_style(style: str | None, hint: str | None = None) -> str:
    """Resolve a style. Explicit wins; 'auto'/None uses the hint, else ink (safe default)."""
    if style and style != "auto":
        return style
    if hint:
        return _HINT_TO_STYLE.get(hint.lower(), "ink")
    return "ink"
