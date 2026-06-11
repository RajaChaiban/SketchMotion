"""LLM auto-router — analyzes the request and decides the route + style + output.

Used by `intake_job` when the user picks `mode=auto`. It asks the LLM to classify intent
(reading the prompt and, when present, an image brief + the video-stylist skill), then
**validates the answer against what's actually available** and falls back to the
deterministic `intake.route()` whenever the LLM is absent (stub), errors, or returns an
infeasible choice. So the agent can only ever *refine* a safe decision, never break it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from worker.intake import Plan, route as deterministic_route
from worker.llm import LLMProvider, extract_json
from worker.skills import choose_style, load_skill

log = logging.getLogger("sketchmotion.router")

ROUTE_TO_JOB = {
    "animate": "generate_job",
    "stylize_video": "stylize_job",
    "stylize_image": "stylize_image_job",
}


@dataclass
class RouteDecision:
    route: str
    job_function: str
    output_kind: str
    style: str
    needs_prompt: bool
    reason: str

    @classmethod
    def from_plan(cls, plan: Plan, reason: str) -> "RouteDecision":
        return cls(plan.route, plan.job_function, plan.output_kind, plan.style,
                   plan.needs_prompt, reason)


class _RouterModel(BaseModel):
    route: Literal["animate", "stylize_video", "stylize_image"]
    style: Literal["ink", "pencil"] = "ink"
    output_kind: Literal["video", "still"] = "video"
    reason: str = ""


def _feasible(route: str, file_kind: str | None, prompt: str) -> bool:
    if route == "stylize_video":
        return file_kind == "video"
    if route == "stylize_image":
        return file_kind == "image"
    if route == "animate":
        return bool(prompt.strip()) or file_kind == "image"
    return False


def _build_router_prompt(prompt: str, file_kind: str | None, image_summary: str) -> str:
    skill = load_skill("video-stylist")
    return f"""You are a routing agent for a sketch-video tool. Decide what the user wants.

Available routes:
- "animate": build a hand-drawn animation FROM a text prompt (optionally using an image as a reference). Output is a video.
- "stylize_video": redraw an existing VIDEO as a hand-drawn sketch. Output is a video.
- "stylize_image": redraw a single IMAGE as a hand-drawn sketch. Output is a still image.

Sketch styles: "ink" (bold outlines + color, good for action/sports/busy) or "pencil" (graphite, good for portraits/calm).
{skill}

What the user supplied:
- prompt: {prompt!r}
- attached file: {file_kind or "none"}
- image content (if any): {image_summary or "n/a"}

Return ONLY JSON: {{"route": "...", "style": "ink|pencil", "output_kind": "video|still", "reason": "one short sentence"}}.
Pick a route that matches what is actually attached (don't choose stylize_video without a video).
"""


def decide(
    provider: LLMProvider,
    *,
    prompt: str,
    file_kind: str | None,
    mode: str = "auto",
    output_kind: str | None = None,
    style: str = "auto",
    image_summary: str = "",
) -> RouteDecision:
    # always compute a safe deterministic baseline first
    base = deterministic_route(prompt=prompt, file_kind=file_kind, mode=mode,
                               output_kind=output_kind, style=style)
    if mode != "auto":
        return RouteDecision.from_plan(base, reason="explicit mode")

    text = None
    try:
        text = provider.analyze(_build_router_prompt(prompt, file_kind, image_summary))
    except Exception as e:  # noqa: BLE001 - never let routing crash the job
        log.warning("router_analyze_failed", extra={"error": str(e)})

    if not text:
        return RouteDecision.from_plan(base, reason="rules (no LLM)")

    try:
        data = json.loads(extract_json(text))
        m = _RouterModel.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        log.warning("router_parse_failed", extra={"error": str(e)})
        return RouteDecision.from_plan(base, reason="rules (unparseable LLM)")

    if not _feasible(m.route, file_kind, prompt):
        return RouteDecision.from_plan(base, reason=f"rules (LLM picked infeasible {m.route})")

    resolved_style = m.style if style == "auto" else choose_style(style)
    needs_prompt = m.route == "animate"
    return RouteDecision(
        route=m.route,
        job_function=ROUTE_TO_JOB[m.route],
        output_kind=m.output_kind,
        style=resolved_style,
        needs_prompt=needs_prompt,
        reason=f"agent: {m.reason}"[:200],
    )
