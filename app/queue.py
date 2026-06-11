"""Redis-backed job queue + status store (arq).

`JobQueue` is the single seam the API and worker share. The API holds one on
`app.state.queue`; tests swap in a fake via `dependency_overrides`, so no Redis is
needed to exercise the HTTP layer.
"""
from __future__ import annotations

from typing import Any


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


class JobQueue:
    JOB_PREFIX = "sm:job:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _key(self, job_id: str) -> str:
        return f"{self.JOB_PREFIX}{job_id}"

    async def enqueue_generate(self, payload: dict, job_id: str | None = None) -> str:
        """Enqueue a generate job and seed its status hash; returns the job id.

        A caller-supplied ``job_id`` lets the API persist the uploaded image under a
        known key *before* enqueue (arq's worker runs in another process).
        """
        job = await self._redis.enqueue_job("generate_job", payload, _job_id=job_id)
        resolved = job.job_id if job is not None else job_id
        if resolved is None:
            raise RuntimeError("failed to enqueue job (duplicate job_id?)")
        await self.set_status(resolved, status="queued", progress_pct=0)
        return resolved

    async def set_status(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress_pct: int | None = None,
        error: str | None = None,
    ) -> None:
        mapping: dict[str, Any] = {}
        if status is not None:
            mapping["status"] = status
        if progress_pct is not None:
            mapping["progress_pct"] = progress_pct
        if error is not None:
            mapping["error"] = error
        if mapping:
            await self._redis.hset(self._key(job_id), mapping=mapping)

    async def enqueue(self, function: str, payload: dict, job_id: str | None = None) -> str:
        """Generic enqueue used by the unified /create intake (routes to any job)."""
        job = await self._redis.enqueue_job(function, payload, _job_id=job_id)
        resolved = job.job_id if job is not None else job_id
        if resolved is None:
            raise RuntimeError(f"failed to enqueue {function} (duplicate job_id?)")
        await self.set_status(resolved, status="queued", progress_pct=0)
        return resolved

    async def enqueue_stylize(self, payload: dict, job_id: str | None = None) -> str:
        job = await self._redis.enqueue_job("stylize_job", payload, _job_id=job_id)
        resolved = job.job_id if job is not None else job_id
        if resolved is None:
            raise RuntimeError("failed to enqueue stylize job (duplicate job_id?)")
        await self.set_status(resolved, status="queued", progress_pct=0)
        return resolved

    async def set_spec(self, job_id: str, spec_json: str) -> None:
        await self._redis.hset(self._key(job_id), mapping={"spec": spec_json})

    async def get_status(self, job_id: str) -> dict | None:
        data = await self._redis.hgetall(self._key(job_id))
        if not data:
            return None
        out = {_decode(k): _decode(v) for k, v in data.items()}
        if "progress_pct" in out:
            try:
                out["progress_pct"] = int(out["progress_pct"])
            except (TypeError, ValueError):
                out["progress_pct"] = 0
        return out


async def create_queue(redis_url: str) -> JobQueue:
    """Build a JobQueue backed by a live arq Redis pool."""
    from arq import create_pool
    from arq.connections import RedisSettings

    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    return JobQueue(redis)
