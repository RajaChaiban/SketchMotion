"""Request/response models for the HTTP layer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

JobState = Literal[
    "queued", "analyzing", "vision", "refining", "compiling", "rendering", "done", "failed"
]


class GenerateResponse(BaseModel):
    job_id: str
    status: JobState = "queued"


class CreateResponse(BaseModel):
    job_id: str
    status: JobState = "queued"
    route: str            # animate | stylize_video | stylize_image
    output_kind: str      # video | still
    style: str


class JobStatus(BaseModel):
    job_id: str
    status: JobState
    progress_pct: int = 0
    error: str | None = None
    route: str | None = None
    output_kind: str | None = None
