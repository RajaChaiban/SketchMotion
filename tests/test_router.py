"""LLM auto-router: uses a valid decision, but always falls back to safe rules."""
from __future__ import annotations

import json

from worker.router import decide


class FakeLLM:
    """Minimal provider stand-in — the router only calls .analyze()."""

    def __init__(self, text):
        self._text = text
        self.called = False

    def analyze(self, prompt):
        self.called = True
        return self._text


def _decision_json(route, style="ink", output_kind="video"):
    return json.dumps({"route": route, "style": style, "output_kind": output_kind, "reason": "because"})


def test_uses_valid_llm_decision():
    llm = FakeLLM(_decision_json("stylize_video", style="pencil"))
    d = decide(llm, prompt="", file_kind="video", style="auto")
    assert d.route == "stylize_video" and d.job_function == "stylize_job"
    assert d.style == "pencil"
    assert d.reason.startswith("agent:")


def test_falls_back_when_no_llm():
    llm = FakeLLM(None)  # stub-like
    d = decide(llm, prompt="a launch", file_kind=None)
    assert d.route == "animate" and "rules" in d.reason


def test_falls_back_on_unparseable():
    llm = FakeLLM("I think you should animate it, definitely!")
    d = decide(llm, prompt="a launch", file_kind=None)
    assert d.route == "animate" and "rules" in d.reason


def test_rejects_infeasible_llm_route():
    # LLM hallucinates stylize_video but no video is attached -> fall back to rules
    llm = FakeLLM(_decision_json("stylize_video"))
    d = decide(llm, prompt="a launch", file_kind=None)
    assert d.route == "animate" and "infeasible" in d.reason


def test_explicit_mode_skips_llm():
    llm = FakeLLM(_decision_json("stylize_image"))
    d = decide(llm, prompt="a recap", file_kind="video", mode="animate")
    assert d.route == "animate"
    assert d.reason == "explicit mode"
    assert llm.called is False


def test_explicit_style_overrides_llm():
    llm = FakeLLM(_decision_json("stylize_video", style="pencil"))
    d = decide(llm, prompt="", file_kind="video", style="ink")  # user forced ink
    assert d.style == "ink"


def test_image_only_auto_routes_to_still_via_llm():
    llm = FakeLLM(_decision_json("stylize_image", output_kind="still"))
    d = decide(llm, prompt="", file_kind="image", style="auto")
    assert d.route == "stylize_image" and d.output_kind == "still"
    assert d.job_function == "stylize_image_job"
