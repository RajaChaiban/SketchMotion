"""OverlaySpec — timestamped, normalized-anchor sketch annotations to composite on a video.

The non-LLM half of overlay mode: the annotations are supplied (by the user now, by a video
model later). Validation enforces the design guardrails — normalized anchors clamped to the
frame, <=40 annotations, and <=30% frame coverage at any instant.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

AnnotationType = Literal[
    "arrow_point", "circle_highlight", "callout_text", "underline",
    "box_outline", "sprite_react", "writeon_caption", "progress_bar",
]
Easing = Literal["pop", "draw_on", "fade"]

_NEEDS_TEXT = {"callout_text", "writeon_caption"}


class Annotation(BaseModel):
    type: AnnotationType
    t_start: float = Field(ge=0)
    t_end: float = Field(gt=0)
    anchor: tuple[float, float, float, float]  # normalized x, y, w, h
    params: dict = Field(default_factory=dict)
    easing: Easing = "draw_on"

    @model_validator(mode="after")
    def _validate(self):
        if self.t_end <= self.t_start:
            raise ValueError("t_end must be greater than t_start")
        x, y, w, h = self.anchor
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("anchor x,y must be normalized to 0..1")
        if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            raise ValueError("anchor w,h must be in (0, 1]")
        if x + w > 1.001 or y + h > 1.001:
            raise ValueError("anchor box extends past the frame; clamp x+w and y+h to <= 1")
        if self.type in _NEEDS_TEXT and not str(self.params.get("text", "")).strip():
            raise ValueError(f"{self.type} requires params.text")
        return self

    @property
    def area(self) -> float:
        return self.anchor[2] * self.anchor[3]


class OverlaySpec(BaseModel):
    source_fps: float = Field(gt=0, le=120)
    source_resolution: tuple[int, int]
    annotations: list[Annotation] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _coverage(self):
        # at every annotation start, the simultaneously-active annotations must cover <=30%
        for a in self.annotations:
            active_area = sum(b.area for b in self.annotations
                              if b.t_start <= a.t_start < b.t_end)
            if active_area > 0.30 + 1e-6:
                raise ValueError(
                    f"annotations cover {active_area:.0%} of the frame at t={a.t_start}s "
                    "(max 30% at once)"
                )
        return self


def validate_overlay_spec(data: dict) -> OverlaySpec:
    return OverlaySpec.model_validate(data)
