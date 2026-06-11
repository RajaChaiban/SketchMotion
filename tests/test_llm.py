"""LLM provider layer: JSON extraction, provider selection, and the Claude-CLI
provider (subprocess mocked — no real CLI call in the default suite)."""
from __future__ import annotations

import os

import pytest

from app.config import Settings
from worker.gemini_client import SpecCompilationError, stub_compile
from worker.llm import (
    ClaudeCliProvider,
    GeminiProvider,
    StubProvider,
    compile_with_retry,
    extract_json,
    get_provider,
    resolve_provider_name,
)
from worker.spec import SceneSpec


# --- extract_json ------------------------------------------------------------

def test_extract_json_from_fenced_block():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_from_prose():
    assert extract_json('Sure! Here you go: {"a": 1} hope that helps') == '{"a": 1}'


def test_extract_json_passthrough():
    assert extract_json('{"a": 1}') == '{"a": 1}'


# --- provider selection ------------------------------------------------------

def test_explicit_provider_wins():
    assert resolve_provider_name(Settings(llm_provider="stub")) == "stub"
    assert resolve_provider_name(Settings(llm_provider="claude_cli")) == "claude_cli"


def test_auto_prefers_gemini_when_key_present():
    assert resolve_provider_name(Settings(llm_provider="auto", gemini_api_key="k")) == "gemini"


def test_auto_falls_to_claude_then_stub(monkeypatch):
    import worker.llm as llm

    monkeypatch.setattr(llm.shutil, "which", lambda _: "/usr/bin/claude")
    assert resolve_provider_name(Settings(llm_provider="auto")) == "claude_cli"
    monkeypatch.setattr(llm.shutil, "which", lambda _: None)
    assert resolve_provider_name(Settings(llm_provider="auto")) == "stub"


def test_get_provider_returns_right_types(monkeypatch):
    import worker.llm as llm

    assert isinstance(get_provider(Settings(llm_provider="stub")), StubProvider)
    assert isinstance(get_provider(Settings(llm_provider="gemini")), GeminiProvider)
    monkeypatch.setattr(llm.shutil, "which", lambda _: "/usr/bin/claude")
    assert isinstance(get_provider(Settings(llm_provider="claude_cli")), ClaudeCliProvider)


# --- claude_cli provider (mocked) --------------------------------------------

def _valid_json(target=10.0) -> str:
    return stub_compile("a launch", target).model_dump_json()


def test_claude_cli_compile_success():
    prov = ClaudeCliProvider(Settings())
    prov._run_cli = lambda prompt: f"```json\n{_valid_json(10)}\n```"
    spec = prov.compile_spec(refined_prompt="a launch", target_duration_s=10.0)
    assert isinstance(spec, SceneSpec)


def test_claude_cli_retries_then_succeeds():
    prov = ClaudeCliProvider(Settings())
    outs = iter(["not json at all", _valid_json(10)])
    prov._run_cli = lambda prompt: next(outs)
    spec = prov.compile_spec(refined_prompt="x", target_duration_s=10.0)
    assert isinstance(spec, SceneSpec)


def test_claude_cli_all_fail_raises():
    prov = ClaudeCliProvider(Settings())
    prov._run_cli = lambda prompt: "never valid"
    with pytest.raises(SpecCompilationError):
        prov.compile_spec(refined_prompt="x", target_duration_s=10.0)


def test_compile_with_retry_passes_validation_feedback():
    seen = []

    def gen(prompt: str) -> str:
        seen.append(prompt)
        return "bad" if len(seen) == 1 else _valid_json(10)

    spec = compile_with_retry(gen, "BASE", 10.0)
    assert isinstance(spec, SceneSpec)
    assert "FAILED VALIDATION" in seen[1]  # error fed back into the 2nd attempt


# --- opt-in: hit the real CLI (costs money/time) -----------------------------

@pytest.mark.claude_cli
@pytest.mark.skipif(not os.getenv("RUN_CLAUDE_CLI"), reason="set RUN_CLAUDE_CLI=1 to run")
def test_real_claude_cli_compiles():
    prov = ClaudeCliProvider(Settings())
    spec = prov.compile_spec(refined_prompt="celebrate a product launch", target_duration_s=12.0)
    assert isinstance(spec, SceneSpec)
    assert spec.scenes[0].type in {"hook_claim", "hook_question", "pattern_interrupt"}
