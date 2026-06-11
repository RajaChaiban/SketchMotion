"""Request/response models for the HTTP layer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

JobState = Literal[
    "queued", "vision", "refining", "compiling", "rendering", "done", "failed"
]


class GenerateResponse(BaseModel):
    job_id: str
    status: JobState = "queued"


class JobStatus(BaseModel):
    job_id: str
    status: JobState
    progress_pct: int = 0
    error: str | None = None
