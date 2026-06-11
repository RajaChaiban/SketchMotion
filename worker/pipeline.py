"""Step orchestration: refine -> (vision) -> compile -> render, with job-status
updates at each stage. Called by the arq worker; also directly unit-testable with a
FakeQueue and no Redis.

Gemini is used only when a key is configured; otherwise the deterministic stub compiler
keeps the whole flow working offline. CPU-bound rendering and blocking Gemini calls run
in a thread so the worker's event loop stays responsive.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import Settings
from app.queue import JobQueue
from render.engine import render_spec
from worker.gemini_client import GeminiClient, ImageBrief, stub_compile
from worker.refine import ContentRejected, refine

log = logging.getLogger("sketchmotion.pipeline")


async def run_generate(
    queue: JobQueue,
    settings: Settings,
    payload: dict,
    client: GeminiClient | None = None,
) -> dict:
    job_id = payload["job_id"]
    prompt = payload["prompt"]
    duration = float(payload["duration_s"])
    aspect = payload.get("aspect", "16:9")
    draft = bool(payload.get("draft", False))
    image_path = payload.get("image_path")
    image_mime = payload.get("image_mime") or "image/png"

    try:
        use_gemini = settings.gemini_enabled
        if use_gemini and client is None:
            client = GeminiClient(settings)

        # Step 1 — vision (only with an image + a key)
        image_brief: ImageBrief | None = None
        if image_path and use_gemini and client is not None:
            await queue.set_status(job_id, status="vision", progress_pct=10)
            data = await asyncio.to_thread(Path(image_path).read_bytes)
            image_brief = await asyncio.to_thread(client.vision, data, image_mime)

        # Step 2 — refine (passthrough hook; raises ContentRejected for blocked IP)
        await queue.set_status(job_id, status="refining", progress_pct=25)
        refined = refine(prompt, image_brief)

        # Step 3 — spec compilation (Gemini, or deterministic stub)
        await queue.set_status(job_id, status="compiling", progress_pct=40)
        if use_gemini and client is not None:
            spec = await asyncio.to_thread(
                lambda: client.compile_spec(
                    refined_prompt=refined.prompt,
                    target_duration_s=duration,
                    aspect=aspect,
                    image_brief=image_brief,
                )
            )
        else:
            spec = stub_compile(refined.prompt, duration, aspect)
        await queue.set_spec(job_id, spec.model_dump_json())

        # Step 5 — render
        await queue.set_status(job_id, status="rendering", progress_pct=70)
        out_path = Path(settings.output_dir) / f"{job_id}.mp4"
        await asyncio.to_thread(render_spec, spec.model_dump(), out_path, draft=draft)

        await queue.set_status(job_id, status="done", progress_pct=100)
        log.info("job_done", extra={"job_id": job_id, "scenes": len(spec.scenes)})
        return {"job_id": job_id, "output": str(out_path)}

    except ContentRejected as e:
        await queue.set_status(job_id, status="failed", error=str(e))
        log.warning("job_rejected", extra={"job_id": job_id, "error": str(e)})
        return {"job_id": job_id, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface a clean failure to the UI
        await queue.set_status(job_id, status="failed", error=f"{type(e).__name__}: {e}")
        log.exception("job_failed", extra={"job_id": job_id})
        return {"job_id": job_id, "error": str(e)}
