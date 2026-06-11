"""Per-pipeline skills: loading + injection + the stylist presets/chooser."""
from __future__ import annotations

from worker.gemini_client import _build_compile_prompt
from worker.skills import STYLE_PRESETS, choose_style, load_skill, style_opts


def test_load_skill_returns_text():
    refiner = load_skill("prompt-refiner")
    stylist = load_skill("video-stylist")
    assert "hook" in refiner.lower()
    assert "ink" in stylist.lower() and "pencil" in stylist.lower()


def test_unknown_skill_is_empty_passthrough():
    assert load_skill("does-not-exist") == ""


def test_skill_is_injected_into_compile_prompt():
    skill = load_skill("prompt-refiner")
    prompt = _build_compile_prompt("a launch", None, 12.0, "16:9", skill=skill)
    assert "Director's skill" in prompt
    assert "hook" in prompt.lower()
    # without a skill, no skill block
    plain = _build_compile_prompt("a launch", None, 12.0, "16:9")
    assert "Director's skill" not in plain


def test_style_opts_per_style():
    assert style_opts("ink")["edge_strength"] == STYLE_PRESETS["ink"]["edge_strength"]
    assert "blur_radius" in style_opts("pencil")
    assert style_opts("nope") == {}


def test_choose_style_resolution():
    assert choose_style("pencil") == "pencil"          # explicit wins
    assert choose_style("auto") == "ink"               # safe default
    assert choose_style("auto", hint="sports") == "ink"
    assert choose_style("auto", hint="portrait") == "pencil"
    assert choose_style(None, hint="face") == "pencil"
