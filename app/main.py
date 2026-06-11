"""FastAPI app: serves the static UI and the generate/job API.

The render/Gemini work happens in the arq worker (`worker/`). This module only
validates input, enqueues, and reports status — it stays renderer- and
Gemini-agnostic.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import filetype

from app.config import Settings, get_settings
from app.models import CreateResponse, GenerateResponse, JobStatus
from app.queue import JobQueue, create_queue
from worker.intake import route as intake_route

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_SUBDIR = "uploads"
VALID_ASPECTS = {"16:9", "9:16", "1:1"}

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.output_dir) / UPLOAD_SUBDIR).mkdir(parents=True, exist_ok=True)
    try:
        app.state.queue = await create_queue(settings.redis_url)
    except Exception:  # pragma: no cover - depends on a live Redis
        app.state.queue = None
    yield
    queue = getattr(app.state, "queue", None)
    if queue is not None:
        try:
            await queue._redis.aclose()
        except Exception:  # pragma: no cover
            pass


app = FastAPI(title="SketchMotion", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- dependencies -----------------------------------------------------------

def get_queue(request: Request) -> JobQueue:
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="job queue unavailable")
    return queue


# --- routes -----------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(page)


@app.post("/generate", response_model=GenerateResponse)
@limiter.limit("30/minute")
async def generate(
    request: Request,
    prompt: str = Form(...),
    duration_s: int = Form(...),
    aspect: str = Form("16:9"),
    captions: bool = Form(True),
    draft: bool = Form(False),
    image: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_queue),
) -> GenerateResponse:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    if len(prompt) > settings.max_prompt_chars:
        raise HTTPException(
            status_code=422,
            detail=f"prompt exceeds {settings.max_prompt_chars} chars",
        )
    if not 5 <= duration_s <= 60:
        raise HTTPException(status_code=422, detail="duration_s must be 5..60")
    if aspect not in VALID_ASPECTS:
        raise HTTPException(status_code=422, detail=f"aspect must be one of {VALID_ASPECTS}")

    job_id = uuid.uuid4().hex
    image_path: str | None = None
    if image is not None and image.filename:
        data = await image.read()
        if len(data) > settings.max_upload_image_bytes:
            raise HTTPException(
                status_code=422,
                detail=f"image exceeds {settings.max_upload_image_mb} MB",
            )
        if not (image.content_type or "").startswith("image/"):
            raise HTTPException(status_code=422, detail="uploaded file is not an image")
        dest = Path(settings.output_dir) / UPLOAD_SUBDIR / f"{job_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(dest, "wb") as fh:
            await fh.write(data)
        image_path = str(dest)

    payload = {
        "job_id": job_id,
        "prompt": prompt,
        "duration_s": duration_s,
        "aspect": aspect,
        "captions": captions,
        "draft": draft,
        "image_path": image_path,
        "image_mime": image.content_type if image else None,
    }
    await queue.enqueue_generate(payload, job_id=job_id)
    return GenerateResponse(job_id=job_id, status="queued")


@app.post("/stylize", response_model=GenerateResponse)
@limiter.limit("10/minute")
async def stylize(
    request: Request,
    style: str = Form("ink"),
    video: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_queue),
) -> GenerateResponse:
    if style not in ("ink", "pencil"):
        raise HTTPException(status_code=422, detail="style must be 'ink' or 'pencil'")
    if not (video.content_type or "").startswith("video/"):
        raise HTTPException(status_code=422, detail="uploaded file is not a video")
    data = await video.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty video upload")
    if len(data) > settings.max_upload_video_bytes:
        raise HTTPException(
            status_code=422, detail=f"video exceeds {settings.max_upload_video_mb} MB"
        )

    job_id = uuid.uuid4().hex
    dest = Path(settings.output_dir) / UPLOAD_SUBDIR / f"{job_id}_src"
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as fh:
        await fh.write(data)

    payload = {"job_id": job_id, "video_path": str(dest), "style": style}
    await queue.enqueue_stylize(payload, job_id=job_id)
    return GenerateResponse(job_id=job_id, status="queued")


def _sniff_kind(data: bytes, content_type: str | None) -> str | None:
    ct = content_type or ""
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    guess = filetype.guess(data[:512])
    if guess is not None:
        if guess.mime.startswith("image/"):
            return "image"
        if guess.mime.startswith("video/"):
            return "video"
    return None


@app.post("/create", response_model=CreateResponse)
@limiter.limit("20/minute")
async def create(
    request: Request,
    prompt: str = Form(""),
    mode: str = Form("auto"),
    output_kind: str | None = Form(None),
    style: str = Form("auto"),
    duration_s: int = Form(15),
    aspect: str = Form("16:9"),
    captions: bool = Form(True),
    file: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_queue),
) -> CreateResponse:
    """Unified intake: user picks options up front; we analyze + route to a pipeline."""
    file_kind: str | None = None
    data: bytes | None = None
    if file is not None and file.filename:
        data = await file.read()
        file_kind = _sniff_kind(data, file.content_type)
        if file_kind is None:
            raise HTTPException(status_code=422, detail="file must be an image or a video")
        if file_kind == "image" and len(data) > settings.max_upload_image_bytes:
            raise HTTPException(status_code=422, detail=f"image exceeds {settings.max_upload_image_mb} MB")
        if file_kind == "video" and len(data) > settings.max_upload_video_bytes:
            raise HTTPException(status_code=422, detail=f"video exceeds {settings.max_upload_video_mb} MB")

    try:
        plan = intake_route(prompt=prompt, file_kind=file_kind, mode=mode,
                            output_kind=output_kind, style=style)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if plan.needs_prompt and not prompt.strip():
        raise HTTPException(status_code=422, detail="this route needs a prompt")
    if len(prompt) > settings.max_prompt_chars:
        raise HTTPException(status_code=422, detail=f"prompt exceeds {settings.max_prompt_chars} chars")
    if plan.job_function == "generate_job" and not 5 <= duration_s <= 60:
        raise HTTPException(status_code=422, detail="duration_s must be 5..60")

    job_id = uuid.uuid4().hex
    upload_dir = Path(settings.output_dir) / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_path: str | None = None
    video_path: str | None = None
    if data is not None and file_kind == "image":
        image_path = str(upload_dir / f"{job_id}.img")
        async with aiofiles.open(image_path, "wb") as fh:
            await fh.write(data)
    elif data is not None and file_kind == "video":
        video_path = str(upload_dir / f"{job_id}_src")
        async with aiofiles.open(video_path, "wb") as fh:
            await fh.write(data)

    payload: dict = {"job_id": job_id}
    if plan.job_function == "generate_job":
        payload.update(prompt=prompt.strip(), duration_s=duration_s, aspect=aspect,
                       captions=captions, draft=False,
                       image_path=image_path, image_mime=(file.content_type if file else None))
    elif plan.job_function == "stylize_job":
        payload.update(video_path=video_path, style=plan.style)
    else:  # stylize_image_job
        payload.update(image_path=image_path, style=plan.style)

    await queue.enqueue(plan.job_function, payload, job_id)
    return CreateResponse(job_id=job_id, status="queued", route=plan.route,
                          output_kind=plan.output_kind, style=plan.style)


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, queue: JobQueue = Depends(get_queue)) -> JobStatus:
    data = await queue.get_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatus(
        job_id=job_id,
        status=data.get("status", "queued"),
        progress_pct=data.get("progress_pct", 0),
        error=data.get("error"),
    )


@app.get("/jobs/{job_id}/video")
async def job_video(job_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    path = Path(settings.output_dir) / f"{job_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="video not ready")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/jobs/{job_id}/image")
async def job_image(job_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    path = Path(settings.output_dir) / f"{job_id}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not ready")
    return FileResponse(path, media_type="image/png", filename=f"{job_id}.png")


@app.get("/jobs/{job_id}/spec")
async def job_spec(job_id: str, queue: JobQueue = Depends(get_queue)) -> JSONResponse:
    data = await queue.get_status(job_id)
    if data is None or "spec" not in data:
        raise HTTPException(status_code=404, detail="spec not available")
    import json

    return JSONResponse(content={"spec": json.loads(data["spec"])})
