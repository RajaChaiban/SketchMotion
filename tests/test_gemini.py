"""Gemini client: vision parse, spec compile with retry/fallback, and the key-free
stub compiler. All mocked at the `_raw_generate` boundary — no network."""
from __future__ import annotations

import pytest

from app.config import Settings
from render.engine import render_frame
from worker.gemini_client import (
    GeminiClient,
    ImageBrief,
    RawResponse,
    SpecCompilationError,
    stub_compile,
)
from worker.spec import SceneSpec


def _settings() -> Settings:
    return Settings(
        gemini_api_key="test-key",
        gemini_spec_model="primary-model",
        gemini_spec_model_fallback="fallback-model",
    )


def _valid_spec_json(target: float = 10.0) -> str:
    return stub_compile("a product launch", target).model_dump_json()


# --- vision ------------------------------------------------------------------

def test_vision_parses_image_brief():
    client = GeminiClient(_settings(), client=object())
    brief_json = ImageBrief(subject="rocket", suggested_sprites=["rocket"]).model_dump_json()
    client._raw_generate = lambda **kw: RawResponse(text=brief_json)
    brief = client.vision(b"\x89PNG fake", "image/png")
    assert brief.subject == "rocket"
    assert brief.suggested_sprites == ["rocket"]


# --- compile: happy path -----------------------------------------------------

def test_compile_spec_success_first_try():
    client = GeminiClient(_settings(), client=object())
    calls = []

    def fake(**kw):
        calls.append(kw["model"])
        return RawResponse(text=_valid_spec_json(10.0))

    client._raw_generate = fake
    spec = client.compile_spec(refined_prompt="a launch", target_duration_s=10.0)
    assert isinstance(spec, SceneSpec)
    assert calls == ["primary-model"]  # no retries needed


# --- compile: retry then success on primary ----------------------------------

def test_compile_spec_retries_then_succeeds():
    client = GeminiClient(_settings(), client=object())
    calls = []
    outputs = ['{"not":"a spec"}', _valid_spec_json(10.0)]

    def fake(**kw):
        calls.append(kw["model"])
        return RawResponse(text=outputs[len(calls) - 1])

    client._raw_generate = fake
    spec = client.compile_spec(refined_prompt="x", target_duration_s=10.0)
    assert isinstance(spec, SceneSpec)
    assert calls == ["primary-model", "primary-model"]


# --- compile: escalates to fallback model on 3rd attempt ---------------------

def test_compile_spec_escalates_to_fallback():
    client = GeminiClient(_settings(), client=object())
    calls = []

    def fake(**kw):
        calls.append(kw["model"])
        if len(calls) < 3:
            return RawResponse(text="garbage not json")
        return RawResponse(text=_valid_spec_json(10.0))

    client._raw_generate = fake
    spec = client.compile_spec(refined_prompt="x", target_duration_s=10.0)
    assert isinstance(spec, SceneSpec)
    assert calls == ["primary-model", "primary-model", "fallback-model"]


# --- compile: all attempts fail ----------------------------------------------

def test_compile_spec_all_fail_raises():
    client = GeminiClient(_settings(), client=object())
    client._raw_generate = lambda **kw: RawResponse(text="never valid")
    with pytest.raises(SpecCompilationError):
        client.compile_spec(refined_prompt="x", target_duration_s=10.0)


# --- stub compiler -----------------------------------------------------------

@pytest.mark.parametrize("target", [5, 15, 30, 45, 60])
@pytest.mark.parametrize("aspect", ["16:9", "9:16", "1:1"])
def test_stub_compile_always_valid(target, aspect):
    spec = stub_compile("celebrate a product launch with confetti", target, aspect)
    assert isinstance(spec, SceneSpec)
    # total matches target exactly; first scene is a hook within 3s
    assert abs(spec.total_duration_s - target) < 0.01
    assert spec.scenes[0].type in {"hook_claim", "hook_question", "pattern_interrupt"}
    assert spec.scenes[0].duration_s <= 3.0
    assert all(0 < s.duration_s <= 8 for s in spec.scenes)


def test_stub_spec_renders():
    spec = stub_compile("launch day", 15, "16:9")
    img = render_frame(spec.model_dump(), 0, 0, 30)
    assert img.size == (1280, 720)
