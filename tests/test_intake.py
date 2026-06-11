"""Intake routing matrix — deterministic, honors options, sensible auto defaults."""
from __future__ import annotations

import pytest

from worker.intake import route


def test_prompt_only_animates():
    p = route(prompt="celebrate a launch", file_kind=None)
    assert (p.route, p.job_function, p.output_kind) == ("animate", "generate_job", "video")
    assert p.needs_prompt


def test_video_auto_stylizes_video():
    p = route(prompt="", file_kind="video")
    assert (p.route, p.job_function, p.output_kind) == ("stylize_video", "stylize_job", "video")
    assert not p.needs_prompt


def test_image_alone_becomes_still_sketch():
    p = route(prompt="", file_kind="image")
    assert (p.route, p.job_function, p.output_kind) == ("stylize_image", "stylize_image_job", "still")


def test_image_plus_prompt_animates_with_reference():
    p = route(prompt="make it pop", file_kind="image")
    assert p.route == "animate" and p.job_function == "generate_job"


def test_image_with_still_output_kind_forces_still():
    p = route(prompt="ignore me", file_kind="image", output_kind="still")
    assert p.route == "stylize_image"


def test_explicit_mode_overrides_auto():
    # force animate even with a video present -> but animate needs a prompt
    p = route(prompt="a recap", file_kind="video", mode="animate")
    assert p.route == "animate"
    # force stylize on an image
    p2 = route(prompt="", file_kind="image", mode="stylize")
    assert p2.route == "stylize_image"


def test_style_resolution():
    assert route(prompt="x", file_kind=None, style="auto").style == "ink"
    assert route(prompt="", file_kind="video", style="pencil").style == "pencil"


@pytest.mark.parametrize("kwargs", [
    {"prompt": "", "file_kind": None},                       # nothing supplied
    {"prompt": "", "file_kind": None, "mode": "stylize"},     # stylize without a file
])
def test_invalid_combos_raise(kwargs):
    with pytest.raises(ValueError):
        route(**kwargs)
