"""arq worker entrypoint.

`generate_job` runs the full pipeline (refine -> vision -> compile -> render) from
`worker/pipeline.py`.

Run with:  uv run arq worker.worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

import asyncio
from pathlib import Path

from app.config import get_settings
from app.queue import JobQueue
from worker.pipeline import run_generate
from worker.skills import style_opts
from worker.stylize import stylize_image, stylize_video


async def generate_job(ctx: dict, payload: dict) -> dict[str, Any]:
    queue = JobQueue(ctx["redis"])
    return await run_generate(queue, get_settings(), payload)


async def stylize_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Phase 7: turn an uploaded real video into a hand-drawn sketch (no LLM)."""
    queue = JobQueue(ctx["redis"])
    settings = get_settings()
    job_id = payload["job_id"]
    try:
        await queue.set_status(job_id, status="rendering", progress_pct=5)
        out_path = Path(settings.output_dir) / f"{job_id}.mp4"
        style = payload.get("style", "ink")
        await asyncio.to_thread(
            stylize_video,
            payload["video_path"],
            out_path,
            style=style,
            opts=style_opts(style),
            max_seconds=settings.max_video_seconds,
            workers=payload.get("workers"),
        )
        await queue.set_status(job_id, status="done", progress_pct=100)
        return {"job_id": job_id, "output": str(out_path)}
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


async def stylize_image_job(ctx: dict, payload: dict) -> dict[str, Any]:
    """Phase 7: sketch a single uploaded image to a PNG still."""
    queue = JobQueue(ctx["redis"])
    settings = get_settings()
    job_id = payload["job_id"]
    try:
        await queue.set_status(job_id, status="rendering", progress_pct=20)
        out_path = Path(settings.output_dir) / f"{job_id}.png"
        style = payload.get("style", "ink")
        await asyncio.to_thread(
            stylize_image, payload["image_path"], out_path, style=style, opts=style_opts(style)
        )
        await queue.set_status(job_id, status="done", progress_pct=100)
        return {"job_id": job_id, "output": str(out_path)}
    except Exception as e:  # noqa: BLE001
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        return {"job_id": job_id, "error": str(e)}


class WorkerSettings:
    functions = [generate_job, stylize_job, stylize_image_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
