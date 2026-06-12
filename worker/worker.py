"""arq worker entrypoint.

`generate_job` runs the full pipeline (refine -> vision -> compile -> render) from
`worker/pipeline.py`.

Run with:  uv run arq worker.worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

import asyncio
import logging
from pathlib import Path

from app.config import get_settings
from app.queue import JobQueue
from worker.pipeline import run_generate
from worker.skills import style_opts
from worker.stylize import stylize_image, stylize_video

log = logging.getLogger("sketchmotion.worker")


async def generate_job(ctx: dict, payload: dict) -> dict[str, Any]:
    queue = JobQueue(ctx["redis"])
    return await run_generate(queue, get_settings(), payload)


async def _do_stylize_video(queue: JobQueue, settings, payload: dict) -> dict[str, Any]:
    job_id = payload["job_id"]
    await queue.set_status(job_id, status="rendering", progress_pct=5)
    out_path = Path(settings.output_dir) / f"{job_id}.mp4"
    style = payload.get("style", "ink")
    await asyncio.to_thread(
        stylize_video, payload["video_path"], out_path,
        style=style, opts=style_opts(style),
        max_seconds=settings.max_video_seconds,
        workers=payload.get("workers") or settings.stylize_workers,
    )
    await queue.set_status(job_id, status="done", progress_pct=100)
    return {"job_id": job_id, "output": str(out_path)}


async def _do_stylize_image(queue: JobQueue, settings, payload: dict) -> dict[str, Any]:
    job_id = payload["job_id"]
    await queue.set_status(job_id, status="rendering", progress_pct=20)
    out_path = Path(settings.output_dir) / f"{job_id}.png"
    style = payload.get("style", "ink")
    await asyncio.to_thread(
        stylize_image, payload["image_path"], out_path, style=style, opts=style_opts(style)
    )
    await queue.set_status(job_id, status="done", progress_pct=100)
    return {"job_id": job_id, "output": str(out_path)}


async def stylize_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Phase 7: turn an uploaded real video into a hand-drawn sketch (no LLM)."""
    queue = JobQueue(ctx["redis"])
    job_id = payload["job_id"]
    try:
        return await _do_stylize_video(queue, get_settings(), payload)
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


async def stylize_image_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Phase 7: sketch a single uploaded image to a PNG still."""
    queue = JobQueue(ctx["redis"])
    job_id = payload["job_id"]
    try:
        return await _do_stylize_image(queue, get_settings(), payload)
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


async def intake_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Unified `mode=auto` entry: the AI agent analyzes the request, then dispatches."""
    from worker.llm import get_provider
    from worker.router import decide

    queue = JobQueue(ctx["redis"])
    settings = get_settings()
    job_id = payload["job_id"]
    opts = payload.get("options", {})
    try:
        await queue.set_status(job_id, status="analyzing", progress_pct=5)
        provider = get_provider(settings)

        # let the agent "see" an attached image for a better routing decision
        image_summary = ""
        image_path = payload.get("image_path")
        if image_path and provider.supports_vision:
            data = await asyncio.to_thread(Path(image_path).read_bytes)
            brief = await asyncio.to_thread(provider.vision, data, payload.get("image_mime") or "image/png")
            if brief is not None:
                image_summary = f"{brief.subject}; {', '.join(brief.objects[:4])}"

        decision = await asyncio.to_thread(
            lambda: decide(
                provider,
                prompt=payload.get("prompt", ""),
                file_kind=payload.get("file_kind"),
                mode="auto",
                output_kind=opts.get("output_kind"),
                style=opts.get("style", "auto"),
                image_summary=image_summary,
            )
        )
        await queue.set_status(job_id, status="analyzing", progress_pct=15)
        await queue.set_meta(job_id, route=decision.route, output_kind=decision.output_kind)
        log.info("intake_route", extra={"job_id": job_id, "route": decision.route, "reason": decision.reason})

        if decision.job_function == "generate_job":
            gen = {
                "job_id": job_id,
                "prompt": payload.get("prompt", ""),
                "duration_s": opts.get("duration_s", 15),
                "aspect": opts.get("aspect", "16:9"),
                "captions": opts.get("captions", True),
                "draft": False,
                "image_path": image_path,
                "image_mime": payload.get("image_mime"),
            }
            return await run_generate(queue, settings, gen, provider)
        if decision.job_function == "stylize_job":
            return await _do_stylize_video(queue, settings, {**payload, "style": decision.style})
        return await _do_stylize_image(queue, settings, {**payload, "style": decision.style})
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


async def annotate_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Overlay mode (non-LLM): composite sketch annotations onto a base video."""
    from worker.overlay import composite_annotations
    from worker.overlay_spec import validate_overlay_spec
    from worker.video_ingest import probe

    queue = JobQueue(ctx["redis"])
    settings = get_settings()
    job_id = payload["job_id"]
    try:
        await queue.set_status(job_id, status="rendering", progress_pct=5)
        base = payload["video_path"]
        info = await asyncio.to_thread(probe, base)
        spec = validate_overlay_spec({
            "source_fps": info["fps"],
            "source_resolution": [info["width"], info["height"]],
            "annotations": payload.get("annotations", []),
        })
        out_path = Path(settings.output_dir) / f"{job_id}.mp4"
        await asyncio.to_thread(composite_annotations, base, spec, out_path)
        await queue.set_status(job_id, status="done", progress_pct=100)
        return {"job_id": job_id, "output": str(out_path)}
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


class WorkerSettings:
    functions = [generate_job, stylize_job, stylize_image_job, intake_job, annotate_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
