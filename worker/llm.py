"""LLM provider layer — the spec compiler is pluggable.

Three backends behind one interface:
- ``gemini``     : the google-genai client (production; needs GEMINI_API_KEY).
- ``claude_cli`` : shells out to the local `claude` CLI for real, prompt-aware spec
                   compilation with no API key (dev-time, on a host where Claude Code runs).
- ``stub``       : deterministic, offline, template-based (CI, no-key fallback).

`get_provider(settings)` resolves the selection (``llm_provider``, default ``auto``).
The pipeline depends only on the `LLMProvider` interface, so swapping backends is config.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Callable, Protocol, runtime_checkable

from pydantic import ValidationError

from app.config import Settings
from worker.gemini_client import (
    GeminiClient,
    ImageBrief,
    SpecCompilationError,
    _build_compile_prompt,
    stub_compile,
)
from worker.spec import SceneSpec, validate_spec

log = logging.getLogger("sketchmotion.llm")


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    supports_vision: bool

    def compile_spec(self, *, refined_prompt: str, target_duration_s: float,
                     aspect: str = "16:9", image_brief: ImageBrief | None = None,
                     skill: str = "") -> SceneSpec: ...

    def vision(self, image_bytes: bytes, mime: str) -> ImageBrief | None: ...


# --- shared helpers ----------------------------------------------------------

def extract_json(text: str) -> str:
    """Pull a JSON object out of a model response (handles ```json fences / prose)."""
    t = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fenced:
        return fenced.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        return t[i : j + 1]
    return t


def compile_with_retry(
    generate: Callable[[str], str],
    base_prompt: str,
    target_duration_s: float,
    attempts: int = 3,
    label: str = "llm",
) -> SceneSpec:
    """Generate -> extract -> validate, appending validation errors and retrying."""
    notes = ""
    last_err: Exception | None = None
    for attempt in range(attempts):
        text = generate(base_prompt + notes)
        try:
            data = json.loads(extract_json(text))
            return validate_spec(data, target_duration_s=target_duration_s)
        except (ValidationError, json.JSONDecodeError, ValueError, KeyError) as e:
            last_err = e
            log.warning("spec_retry", extra={"provider": label, "attempt": attempt, "error": str(e)})
            notes = (
                "\n\nYOUR PREVIOUS OUTPUT FAILED VALIDATION:\n"
                f"{e}\n"
                "Return ONLY corrected JSON that satisfies every rule."
            )
    raise SpecCompilationError(str(last_err))


# --- providers ---------------------------------------------------------------

class StubProvider:
    name = "stub"
    supports_vision = False

    def compile_spec(self, *, refined_prompt, target_duration_s, aspect="16:9", image_brief=None, skill=""):
        return stub_compile(refined_prompt, target_duration_s, aspect)

    def vision(self, image_bytes, mime):
        return None


class GeminiProvider:
    name = "gemini"
    supports_vision = True

    def __init__(self, settings: Settings, client=None) -> None:
        self.client = GeminiClient(settings, client=client)

    def compile_spec(self, *, refined_prompt, target_duration_s, aspect="16:9", image_brief=None, skill=""):
        return self.client.compile_spec(
            refined_prompt=refined_prompt,
            target_duration_s=target_duration_s,
            aspect=aspect,
            image_brief=image_brief,
            skill=skill,
        )

    def vision(self, image_bytes, mime):
        return self.client.vision(image_bytes, mime)


class ClaudeCliProvider:
    """Spec compilation via the local `claude` CLI (`claude -p ... --output-format json`)."""

    name = "claude_cli"
    supports_vision = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _run_cli(self, prompt: str) -> str:
        cmd = [self.settings.claude_cli_path, "-p", prompt, "--output-format", "json"]
        if self.settings.claude_cli_model:
            cmd += ["--model", self.settings.claude_cli_model]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",      # CLI emits UTF-8; Windows default cp1252 would crash on em-dashes/arrows
            errors="replace",
            timeout=self.settings.claude_cli_timeout,
        )
        if proc.returncode != 0:
            raise SpecCompilationError(f"claude CLI exited {proc.returncode}: {proc.stderr[:300]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise SpecCompilationError(f"claude CLI returned non-JSON envelope: {e}")
        if envelope.get("is_error"):
            raise SpecCompilationError(f"claude CLI error: {envelope.get('result', '')[:300]}")
        usage = envelope.get("usage") or {}
        log.info(
            "claude_cli_call",
            extra={"cost_usd": envelope.get("total_cost_usd"),
                   "output_tokens": usage.get("output_tokens")},
        )
        return envelope.get("result", "")

    def compile_spec(self, *, refined_prompt, target_duration_s, aspect="16:9", image_brief=None, skill=""):
        base = _build_compile_prompt(refined_prompt, image_brief, target_duration_s, aspect, skill)
        base += "\n\nIMPORTANT: respond with ONLY the JSON object — no prose, no markdown fences."
        return compile_with_retry(self._run_cli, base, target_duration_s, label="claude_cli")

    def vision(self, image_bytes, mime):
        return None  # CLI vision is deferred; production uses Gemini for images


# --- factory -----------------------------------------------------------------

def _claude_available(settings: Settings) -> bool:
    return shutil.which(settings.claude_cli_path) is not None


def resolve_provider_name(settings: Settings) -> str:
    p = (settings.llm_provider or "auto").lower()
    if p != "auto":
        return p
    if settings.gemini_enabled:
        return "gemini"
    if _claude_available(settings):
        return "claude_cli"
    return "stub"


def get_provider(settings: Settings) -> LLMProvider:
    name = resolve_provider_name(settings)
    if name == "gemini":
        return GeminiProvider(settings)
    if name == "claude_cli":
        return ClaudeCliProvider(settings)
    return StubProvider()
