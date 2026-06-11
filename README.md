# SketchMotion

Prompt (+ optional image) → a hand-drawn, sketch-style MP4. **LLM compiles the spec,
Python renders it.** Gemini emits a Pydantic-validated `SceneSpec`; a deterministic PIL +
ffmpeg engine draws the frames.

See [`docs/specs/2026-06-11-sketchmotion-design.md`](docs/specs/2026-06-11-sketchmotion-design.md)
for the design and [`docs/product-plan.md`](docs/product-plan.md) for the full roadmap
(overlay mode, style learning, brand kits — deferred).

## Run (Docker)

```bash
cp .env.example .env          # GEMINI_API_KEY can stay empty -> stub spec compiler
docker compose up --build
# UI:  http://localhost:8000
```

## Run (local, no Docker)

```bash
uv sync
# terminal 1 — Redis (or: docker run -p 6379:6379 redis:7-alpine)
# terminal 2 — API:
uv run uvicorn app.main:app --reload --port 8000
# terminal 3 — worker:
uv run arq worker.worker.WorkerSettings
```

## Test

```bash
uv run pytest                 # Gemini mocked; no API key needed
uv run pytest -m live         # opt-in: hits the real Gemini API (needs GEMINI_API_KEY)
```

## Status

Building **Phases 1–5** (generate mode + static UI). Gemini runs behind mocks / a
deterministic stub until a `GEMINI_API_KEY` is supplied — no code change to go live.

| Phase | What | State |
|---|---|---|
| 1 | Skeleton: API, queue, worker, UI shell | ✅ |
| 2 | Render library (primitives + engine, 11 scene types) | ✅ |
| 3 | `SceneSpec` schema + validation | ✅ |
| 4 | Gemini client (mocked) + stub compiler | ✅ |
| 5 | Full pipeline + API wiring (verified live) | ✅ |

**Generate mode is complete and runs end-to-end** (105 tests passing).

### Spec compiler is pluggable (`LLM_PROVIDER`)

| Provider | When | Needs |
|---|---|---|
| `gemini` | production | `GEMINI_API_KEY` |
| `claude_cli` | dev — real prompt-aware specs now | local `claude` CLI on PATH |
| `stub` | CI / offline | nothing (deterministic templates) |

`auto` (default) picks gemini if a key is set, else the local `claude` CLI, else stub.
So on a dev box running Claude Code you get real prompt-aware compilation with **no API
key**; production just sets `LLM_PROVIDER=gemini` + the key — no code change.

### Phase 7 — Sketch a real video (built)

Turn real footage into a hand-drawn sketch — **no API key** (pure pixel filter).

```bash
uv run python -m worker.stylize in.mp4 out.mp4 --style ink     # or --style pencil
# or in the browser: the "Sketch a real video" card -> POST /stylize
```

`render/sketch_filter.py` (ink = bold outlines + flat color on paper; pencil = graphite
color-dodge), `worker/video_ingest.py` (ffprobe + frame extract), `worker/stylize.py`
(frames → sketch → re-encode, **original audio preserved**, parallel `--workers`). The
LLM half of overlay mode (detect key moments → composite arrows/callouts) is still
deferred until a video-capable model key is configured — it will layer on these frames.

Deferred (see [`docs/product-plan.md`](docs/product-plan.md)): Phase 6 TTS/webhooks,
7b overlay annotations (LLM), 8 style learning, 9 hardening, 10 brand kits/captions.
