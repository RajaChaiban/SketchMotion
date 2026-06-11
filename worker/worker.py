"""arq worker entrypoint.

Phase 1 ships a dummy `generate_job` that just advances the status hash to `done`,
proving the API -> Redis -> worker round-trip. Phase 5 replaces the body with the
real pipeline (`worker/pipeline.py`).

Run with:  uv run arq worker.worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.queue import JobQueue


async def generate_job(ctx: dict, payload: dict) -> dict[str, Any]:
    queue = JobQueue(ctx["redis"])
    job_id = payload["job_id"]
    # Phase 1: no real work yet — walk the status lifecycle so the wire is provable.
    await queue.set_status(job_id, status="compiling", progress_pct=30)
    await queue.set_status(job_id, status="rendering", progress_pct=70)
    await queue.set_status(job_id, status="done", progress_pct=100)
    return {"job_id": job_id, "phase": 1}


class WorkerSettings:
    functions = [generate_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
