"""SceneSpec — the validated contract between the LLM and the renderer.

Gemini emits JSON; this module is the gate. Per-scene ``params`` are validated against
a type-specific model (a hand-rolled discriminated union keyed on ``Scene.type``), and
the spec-level invariants (hook first, duration fidelity, valid palette) are enforced in
Pydantic, never left to the model. `validate_spec()` is the one entrypoint the pipeline
calls; pass ``target_duration_s`` to enforce the ±10% duration budget.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator

from render.engine import ASPECT_PRESETS

SceneType = Literal[
    "hook_claim", "hook_question", "pattern_interrupt",
    "title_writeon", "boxes_popin", "object_hop",
    "arrow_flow", "celebration", "end_card",
    "camera_pan", "custom_sprite_path",
    "basketball_tip", "scoreboard",
]

HOOK_TYPES: frozenset[str] = frozenset({"hook_claim", "hook_question", "pattern_interrupt"})

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_hex(c: str) -> bool:
    return bool(_HEX.match(c))


# --- per-type param models ---------------------------------------------------

class TextParams(BaseModel):
    text: str = Field(min_length=1, max_length=160)


class BoxesParams(BaseModel):
    items: list[str] = Field(min_length=2, max_length=6)


class HopParams(BaseModel):
    sprite: str = "ball"
    hops: int = Field(default=3, ge=1, le=6)


class ArrowParams(BaseModel):
    steps: list[str] = Field(min_length=2, max_length=4)


class CelebrationParams(BaseModel):
    label: str | None = Field(default=None, max_length=80)


class EndCardParams(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    subtitle: str | None = Field(default=None, max_length=120)


class CameraPanParams(BaseModel):
    items: list[str] = Field(min_length=1, max_length=6)


class CustomSpriteParams(BaseModel):
    sprite: str = "star"
    path: list[tuple[float, float]] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def _normalized(self):
        for x, y in self.path:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("path coordinates must be normalized to 0..1")
        return self


class BasketballTipParams(BaseModel):
    shooter: str | None = Field(default=None, max_length=40)
    tipper: str | None = Field(default=None, max_length=40)


class ScoreboardParams(BaseModel):
    away: str = Field(default="AWAY", max_length=12)
    home: str = Field(default="HOME", max_length=12)
    away_score: int = Field(default=0, ge=0, le=300)
    home_score: int = Field(default=0, ge=0, le=300)
    clock: str = Field(default="0:00", max_length=10)


PARAM_MODELS: dict[str, type[BaseModel]] = {
    "hook_claim": TextParams,
    "hook_question": TextParams,
    "pattern_interrupt": TextParams,
    "title_writeon": TextParams,
    "boxes_popin": BoxesParams,
    "object_hop": HopParams,
    "arrow_flow": ArrowParams,
    "celebration": CelebrationParams,
    "end_card": EndCardParams,
    "camera_pan": CameraPanParams,
    "custom_sprite_path": CustomSpriteParams,
    "basketball_tip": BasketballTipParams,
    "scoreboard": ScoreboardParams,
}


# --- scene + spec ------------------------------------------------------------

class Scene(BaseModel):
    type: SceneType
    duration_s: float = Field(gt=0, le=8)
    params: dict = Field(default_factory=dict)
    caption: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_params(self):
        model = PARAM_MODELS[self.type]
        validated = model.model_validate(self.params)
        object.__setattr__(self, "params", validated.model_dump())
        return self


class SceneSpec(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    fps: Literal[24, 30] = 30
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    resolution: tuple[int, int] | None = None
    palette_override: list[str] | None = None
    scenes: list[Scene] = Field(min_length=2, max_length=12)
    total_duration_s: float = Field(gt=0, le=60)

    @model_validator(mode="after")
    def _spec_invariants(self, info: ValidationInfo):
        # resolution is derived, never LLM-chosen
        if self.resolution is None:
            object.__setattr__(self, "resolution", ASPECT_PRESETS[self.aspect])

        # hook enforcement
        first = self.scenes[0]
        if first.type not in HOOK_TYPES:
            raise ValueError(
                f"scenes[0].type must be a hook ({sorted(HOOK_TYPES)}), got {first.type!r}"
            )
        if first.duration_s > 3.0:
            raise ValueError("hook scene (scenes[0]) duration_s must be <= 3.0")

        # total_duration_s must agree with the sum of scene budgets
        total = sum(s.duration_s for s in self.scenes)
        if abs(total - self.total_duration_s) > 0.5:
            raise ValueError(
                f"total_duration_s ({self.total_duration_s}) must equal the sum of "
                f"scene durations ({total:.2f})"
            )

        # duration fidelity: within 10% of the user's target (supplied via context)
        ctx = info.context or {}
        target = ctx.get("target_duration_s")
        if target:
            if abs(self.total_duration_s - target) > 0.10 * target:
                raise ValueError(
                    f"total_duration_s ({self.total_duration_s}) is not within 10% of "
                    f"target ({target})"
                )

        # palette override must be valid hex
        if self.palette_override:
            for c in self.palette_override:
                if not _is_hex(c):
                    raise ValueError(f"palette_override contains invalid hex color {c!r}")
        return self


def validate_spec(data: dict, target_duration_s: float | None = None) -> SceneSpec:
    """Validate raw spec data, optionally enforcing the ±10% duration budget."""
    context = {"target_duration_s": target_duration_s} if target_duration_s else None
    return SceneSpec.model_validate(data, context=context)
