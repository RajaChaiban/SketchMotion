"""Refinement passthrough + content screen."""
from __future__ import annotations

import pytest

from worker.gemini_client import ImageBrief
from worker.refine import ContentRejected, RefinedBrief, refine, screen_prompt


def test_passthrough_trims_and_extracts_keywords():
    rb = refine("  Launch our new app today  ")
    assert isinstance(rb, RefinedBrief)
    assert rb.prompt == "Launch our new app today"
    assert "Launch" in rb.keywords


def test_image_brief_sprites_folded_in():
    brief = ImageBrief(subject="rocket", suggested_sprites=["rocket", "star"])
    rb = refine("a launch", image_brief=brief)
    assert "rocket" in rb.keywords


@pytest.mark.parametrize("bad", ["a video of Batman flying", "Make MICKEY MOUSE dance"])
def test_blocked_content_rejected(bad):
    with pytest.raises(ContentRejected):
        screen_prompt(bad)
    with pytest.raises(ContentRejected):
        refine(bad)


def test_clean_prompt_passes_screen():
    screen_prompt("an original robot mascot celebrating")  # no raise
