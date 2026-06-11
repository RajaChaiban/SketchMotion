"""All Gemini API calls live here (Step 1 vision, Step 3 spec compilation), plus a
deterministic stub compiler used when no API key is configured.

Tests mock at the `_raw_generate` boundary (canned `RawResponse`s) rather than the
HTTP layer — more robust than respx against the google-genai SDK's own transport. The
real `_raw_generate` is wrapped with tenacity backoff for 429/5xx.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import Settings
from render.engine import ASPECT_PRESETS
from render.palette import PALETTE
from worker.spec import SceneSpec, validate_spec

log = logging.getLogger("sketchmotion.gemini")


class GeminiError(RuntimeError):
    pass


class SpecCompilationError(GeminiError):
    """Spec failed validation on every attempt (including the fallback model)."""


# --- vision schema -----------------------------------------------------------

class ImageBrief(BaseModel):
    subject: str = ""
    objects: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    mood: str = ""
    text_in_image: list[str] = Field(default_factory=list)
    suggested_sprites: list[str] = Field(default_factory=list)


@dataclass
class RawResponse:
    text: str
    usage: dict | None = None


def _is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in {429, 500, 502, 503, 504}


# --- prompt construction -----------------------------------------------------

PRIMITIVE_CATALOG = """
Scene types (pick from these only):
- hook_claim / hook_question / pattern_interrupt: a bold opening line. params={"text": str}. MUST be scenes[0], duration<=3.
- title_writeon: a written-on title. params={"text": str}.
- boxes_popin: 2-6 labeled boxes that pop in. params={"items": [str,...]}.
- object_hop: a sprite hopping across. params={"sprite": "ball|star|rocket|bulb|heart|trophy|person", "hops": int}.
- arrow_flow: 2-4 boxes connected by arrows. params={"steps": [str,...]}.
- celebration: confetti + trophy. params={"label": str}.
- end_card: closing title. params={"title": str, "subtitle": str?}.
- camera_pan: pan across 1-6 sprites. params={"items": [str,...]}.
- custom_sprite_path: a sprite along a normalized path. params={"sprite": str, "path": [[x,y],...]} (x,y in 0..1).
"""

VISION_INSTRUCTION = (
    "Analyze this image for an animation brief. Identify the main subject, key objects, "
    "the dominant colors as hex codes, the overall mood, any visible text, and which "
    "sketch sprites best represent it. Return JSON matching the schema."
)


def _build_compile_prompt(refined_prompt: str, image_brief: ImageBrief | None,
                          target_duration_s: float, aspect: str) -> str:
    brief_json = image_brief.model_dump_json() if image_brief else "null"
    return f"""You are a motion-graphics director. Compile the request into a SceneSpec JSON
for a hand-drawn sketch animation. Output ONLY JSON.

Rules:
- scenes[0] MUST be a hook (hook_claim/hook_question/pattern_interrupt), duration_s <= 3.
- 2 to 12 scenes; each scene duration_s in (0, 8].
- total_duration_s MUST equal the sum of scene durations AND be within 10% of the target.
- target_duration_s = {target_duration_s}; aspect = {aspect}.
- Set "title", "fps" (24 or 30), "aspect"="{aspect}", "total_duration_s", and "scenes".
- Each scene may include an optional "caption" (short, for muted autoplay).
- Use plain ASCII in all text/labels/captions — no arrows (→ ↑), bullets, or smart quotes; write "to" or "->".
{PRIMITIVE_CATALOG}
User prompt: {refined_prompt!r}
Image brief (may be null): {brief_json}
"""


# --- client ------------------------------------------------------------------

class GeminiClient:
    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:  # pragma: no cover - requires a real key/network
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _raw_generate(self, *, model: str, contents, response_schema, temperature: float) -> RawResponse:
        from google.genai import types  # pragma: no cover - exercised only live

        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
        )
        resp = self.client.models.generate_content(model=model, contents=contents, config=cfg)
        usage = None
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            usage = {
                "prompt_tokens": getattr(um, "prompt_token_count", None),
                "output_tokens": getattr(um, "candidates_token_count", None),
                "total_tokens": getattr(um, "total_token_count", None),
            }
        return RawResponse(text=resp.text, usage=usage)

    def _log_usage(self, step: str, model: str, raw: RawResponse) -> None:
        log.info("gemini_call", extra={"step": step, "model": model, "usage": raw.usage})

    # Step 1
    def vision(self, image_bytes: bytes, mime: str) -> ImageBrief:
        from google.genai import types  # pragma: no cover - exercised only live

        contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime), VISION_INSTRUCTION]
        raw = self._raw_generate(
            model=self.settings.gemini_vision_model,
            contents=contents,
            response_schema=ImageBrief,
            temperature=0.4,
        )
        self._log_usage("vision", self.settings.gemini_vision_model, raw)
        return ImageBrief.model_validate_json(raw.text)

    # Step 3
    def compile_spec(
        self,
        *,
        refined_prompt: str,
        target_duration_s: float,
        aspect: str = "16:9",
        image_brief: ImageBrief | None = None,
    ) -> SceneSpec:
        base = _build_compile_prompt(refined_prompt, image_brief, target_duration_s, aspect)
        primary = self.settings.gemini_spec_model
        fallback = self.settings.gemini_spec_model_fallback
        # 2 retries on the primary, then escalate to the fallback pro model.
        models = [primary, primary, fallback]
        notes = ""
        last_err: Exception | None = None
        for attempt, model in enumerate(models):
            try:
                raw = self._raw_generate(
                    model=model,
                    contents=[base + notes],
                    response_schema=SceneSpec,
                    temperature=0.3,
                )
                self._log_usage("compile", model, raw)
                data = json.loads(raw.text)
                return validate_spec(data, target_duration_s=target_duration_s)
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_err = e
                log.warning("spec_compile_retry", extra={"attempt": attempt, "model": model, "error": str(e)})
                notes = (
                    "\n\nYOUR PREVIOUS OUTPUT FAILED VALIDATION:\n"
                    f"{e}\n"
                    "Return corrected JSON that satisfies every rule."
                )
        raise SpecCompilationError(str(last_err))


# --- stub compiler (no API key) ---------------------------------------------

def _words(prompt: str, n: int) -> list[str]:
    found = [w.capitalize() for w in re.findall(r"[A-Za-z0-9']+", prompt)][:n]
    while len(found) < 2:
        found.append(["Idea", "Plan", "Ship", "Grow"][len(found)])
    return found


def stub_compile(prompt: str, target_duration_s: float, aspect: str = "16:9",
                 palette: list[str] | None = None) -> SceneSpec:
    """Deterministic, key-free spec compiler. Always returns a valid, renderable spec
    whose total duration exactly equals the target."""
    target = float(target_duration_s)
    hook_d = round(min(3.0, max(1.5, target * 0.2)), 2)
    end_d = round(min(2.5, max(1.5, target * 0.18)), 2)
    mid_budget = round(target - hook_d - end_d, 2)
    if mid_budget < 2.0:  # tiny targets: shrink the bookends
        hook_d = round(max(1.0, target * 0.34), 2)
        end_d = round(max(1.0, target * 0.33), 2)
        mid_budget = round(target - hook_d - end_d, 2)

    n_mid = max(2, min(10, math.ceil(mid_budget / 6.0)))
    each = mid_budget / n_mid
    words = _words(prompt, 4)

    mid_kinds = ["boxes_popin", "arrow_flow", "celebration", "camera_pan"]
    scenes: list[dict] = [
        {"type": "hook_claim", "duration_s": hook_d,
         "params": {"text": (prompt.strip()[:120] or "Watch this")},
         "caption": prompt.strip()[:120] or None},
    ]
    used = 0.0
    for i in range(n_mid):
        kind = mid_kinds[i % len(mid_kinds)]
        d = round(each, 2) if i < n_mid - 1 else round(mid_budget - used, 2)
        used = round(used + d, 2)
        scenes.append({"type": kind, "duration_s": d, "params": _stub_params(kind, words)})

    scenes.append({"type": "end_card", "duration_s": end_d,
                   "params": {"title": words[0], "subtitle": "made with SketchMotion"}})

    data = {
        "title": (prompt.strip()[:80] or "Sketch"),
        "fps": 30,
        "aspect": aspect,
        "palette_override": palette,
        "scenes": scenes,
        "total_duration_s": round(target, 2),
    }
    return validate_spec(data, target_duration_s=target)


def _stub_params(kind: str, words: list[str]) -> dict:
    if kind == "boxes_popin":
        return {"items": words[:3]}
    if kind == "arrow_flow":
        return {"steps": words[:3]}
    if kind == "celebration":
        return {"label": f"{words[0]}!"}
    if kind == "camera_pan":
        return {"items": ["star", "bulb", "rocket"]}
    return {}
