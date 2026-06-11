"""Step 2 — prompt refinement hook (Raja's skill layer plugs in here).

Phase 4 ships a passthrough `refine()` plus a content screen so the rest of the
pipeline can be built and tested independently. The real skill
(`skills/prompt-refiner/SKILL.md`) will later expand this into archetype selection,
pacing rules, and few-shot injection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class ContentRejected(ValueError):
    """Prompt asks for disallowed content (real public figures / copyrighted IP)."""


# Minimal, extendable denylist — the skill layer will replace this with real policy.
_BLOCKED = [
    "mickey mouse", "batman", "superman", "spider-man", "spiderman", "harry potter",
    "elsa", "pikachu", "mario", "darth vader", "james bond",
]


def screen_prompt(prompt: str) -> None:
    low = prompt.lower()
    for term in _BLOCKED:
        if term in low:
            raise ContentRejected(
                f"prompt references protected/copyrighted content ({term!r}); "
                "please describe an original character instead"
            )


@dataclass
class RefinedBrief:
    prompt: str
    archetype_hint: str | None = None
    notes: str = ""
    keywords: list[str] = field(default_factory=list)


def refine(user_prompt: str, image_brief=None) -> RefinedBrief:
    """Passthrough refinement. Screens content, extracts keywords, no LLM call yet."""
    prompt = user_prompt.strip()
    screen_prompt(prompt)
    keywords = re.findall(r"[A-Za-z0-9']+", prompt)[:8]
    if image_brief is not None:
        # fold a couple of image cues into the brief so compilation can use them
        extra = list(getattr(image_brief, "suggested_sprites", []) or [])[:3]
        keywords = (keywords + extra)[:10]
    return RefinedBrief(prompt=prompt, keywords=keywords)
