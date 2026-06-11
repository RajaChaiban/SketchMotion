"""Shared fixtures. The HTTP layer is exercised with no Redis: a FakeQueue is
injected via dependency_overrides, and httpx ASGITransport skips lifespan (so the
real arq pool is never created)."""
from __future__ import annotations

import httpx
import pytest

from app.main import app, get_queue


class FakeQueue:
    """In-memory stand-in for JobQueue — same surface, dict-backed."""

    def __init__(self) -> None:
        self.enqueued: list[dict] = []
        self.jobs: dict[str, dict] = {}

    async def enqueue_generate(self, payload: dict, job_id: str | None = None) -> str:
        jid = job_id or f"job{len(self.enqueued)}"
        self.enqueued.append(payload)
        self.jobs[jid] = {"status": "queued", "progress_pct": 0}
        return jid

    async def set_status(self, job_id, *, status=None, progress_pct=None, error=None):
        rec = self.jobs.setdefault(job_id, {})
        if status is not None:
            rec["status"] = status
        if progress_pct is not None:
            rec["progress_pct"] = progress_pct
        if error is not None:
            rec["error"] = error

    async def enqueue_stylize(self, payload: dict, job_id: str | None = None) -> str:
        jid = job_id or f"job{len(self.enqueued)}"
        self.enqueued.append(payload)
        self.jobs[jid] = {"status": "queued", "progress_pct": 0}
        return jid

    async def set_spec(self, job_id, spec_json: str):
        self.jobs.setdefault(job_id, {})["spec"] = spec_json

    async def get_status(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(fake_queue):
    app.dependency_overrides[get_queue] = lambda: fake_queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
