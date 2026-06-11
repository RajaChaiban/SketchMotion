"""arq worker entrypoint.

`generate_job` runs the full pipeline (refine -> vision -> compile -> render) from
`worker/pipeline.py`.

Run with:  uv run arq worker.worker.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.queue import JobQueue
from worker.pipeline import run_generate


async def generate_job(ctx: dict, payload: dict) -> dict[str, Any]:
    queue = JobQueue(ctx["redis"])
    return await run_generate(queue, get_settings(), payload)


class WorkerSettings:
    functions = [generate_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
