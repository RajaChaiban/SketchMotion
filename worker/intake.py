"""Ingestion / routing — the single decision point for the unified intake.

Given what the user supplied (prompt / image / video) and the options they picked
(mode, output kind, style), decide WHICH pipeline runs. Deterministic and pure (no I/O),
so it's fully testable; the endpoint layer adds file paths and enqueues the chosen job.

Routes:
- animate        -> generate_job       (prompt [+ image] -> sketch VIDEO)
- stylize_video  -> stylize_job        (video -> sketch VIDEO)
- stylize_image  -> stylize_image_job  (image -> sketch STILL)
"""
from __future__ import annotations

from dataclasses import dataclass

from worker.skills import choose_style

FileKind = str  # "image" | "video" | None


@dataclass
class Plan:
    route: str          # animate | stylize_video | stylize_image
    job_function: str   # generate_job | stylize_job | stylize_image_job
    output_kind: str    # video | still
    style: str          # resolved sketch style (ink | pencil | ...)
    needs_prompt: bool  # whether the endpoint must have a non-empty prompt


def route(
    *,
    prompt: str | None,
    file_kind: FileKind | None,
    mode: str = "auto",
    output_kind: str | None = None,
    style: str = "auto",
) -> Plan:
    prompt = (prompt or "").strip()
    mode = (mode or "auto").lower()
    resolved_style = choose_style(style)

    def animate() -> Plan:
        return Plan("animate", "generate_job", "video", resolved_style, needs_prompt=True)

    def stylize_video() -> Plan:
        if file_kind != "video":
            raise ValueError("stylize_video needs a video upload")
        return Plan("stylize_video", "stylize_job", "video", resolved_style, needs_prompt=False)

    def stylize_image() -> Plan:
        if file_kind != "image":
            raise ValueError("stylize_image needs an image upload")
        return Plan("stylize_image", "stylize_image_job", "still", resolved_style, needs_prompt=False)

    # explicit overrides win
    if mode == "animate":
        return animate()
    if mode == "stylize":
        if file_kind == "video":
            return stylize_video()
        if file_kind == "image":
            return stylize_image()
        raise ValueError("stylize mode needs an image or video upload")

    # mode == auto: decide from inputs + the requested output kind
    if file_kind == "video":
        return stylize_video()
    if file_kind == "image":
        if output_kind == "still" or not prompt:
            return stylize_image()
        return animate()  # image acts as a reference for an animation
    if prompt:
        return animate()
    raise ValueError("provide a prompt, an image, or a video")
