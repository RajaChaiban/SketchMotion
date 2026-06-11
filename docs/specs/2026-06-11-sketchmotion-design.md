# SketchMotion — Design Spec

**Date:** 2026-06-11
**Status:** Approved — executing Phases 1–5
**Owner:** Raja

> Prompt (+ optional image) → validated JSON scene spec → deterministic hand-drawn
> sketch-style MP4. Gemini compiles the spec; Python renders it. This document records
> the design decisions agreed in brainstorming. The full multi-mode product plan
> (overlay mode, style learning, brand kits, captions, TTS) lives in
> [`product-plan.md`](../product-plan.md) and is **deferred** beyond this session.

---

## 1. Core principle

**LLM as spec compiler, Python as renderer.** Gemini never writes render code at
runtime; it emits a Pydantic-validated `SceneSpec` JSON that maps to a fixed library
of animation primitives. Every Gemini response is re-validated in Python even when a
`response_schema` was supplied — structured output is good, not guaranteed.

## 2. Decisions locked in brainstorming (deltas from the raw plan)

| Decision | Choice | Why |
|---|---|---|
| Location | New standalone repo `python/sketchmotion/` | Separate from `Auto_marketing`; matches spec repo name. |
| UI | FastAPI serves a single static page (`app/static/index.html` + vanilla JS) at `/` | "User types a prompt → sketch is created" loop with **no separate Node/Next stack**; whole product is one `docker compose up`. A richer frontend can be grafted later. |
| Render primitives | **Built from scratch** | The "existing World Cup script" referenced in the raw plan does not exist on disk (grep of `python/` found zero matches). |
| Gemini API key | **Not available yet** | All Gemini calls go through `gemini_client.py`; unit tests mock the HTTP layer with `respx`. When no `GEMINI_API_KEY` is set, the worker falls back to a deterministic **stub spec compiler** so the end-to-end UI still produces a real MP4 today. Drop in a key → flip to live, no code change. |
| Session scope | **Phases 1–5 + the static UI** | Skeleton → render engine → schema → Gemini (mocked) → full pipeline+API. Overlay / style / marketing layers deferred. |

## 3. Architecture (this session)

```
Browser (static page at /)
  prompt + optional image + duration_s + aspect
      |  POST /generate (multipart)
      v
FastAPI  ──>  returns {job_id, status:"queued"} immediately
      |
      v
arq job queue (Redis)  ── job status hash in Redis
      |
      v
Worker pipeline (worker/pipeline.py):
  Step 1  Vision pass        gemini_client.vision()      (image -> ImageBrief)   [skipped if no image]
  Step 2  Prompt refinement  worker/refine.py            (passthrough hook; Raja's skill later)
  Step 3  Spec compilation   gemini_client.compile_spec() (-> SceneSpec)  OR stub_compile() if no key
  Step 4  Validation         Pydantic, retry loop (2 retries -> fallback model -> fail)
  Step 5  Render             render/engine.py  (PIL frames -> ffmpeg -> MP4)
      |
      v
/jobs/{id}/video  streams OUTPUT_DIR/{job_id}.mp4
```

Status lifecycle: `queued → vision → refining → compiling → rendering → done | failed`,
each written to the Redis job hash with `progress_pct` and optional `error`.

## 4. Components & boundaries

- **`app/`** — HTTP only. `main.py` (routes + static mount), `config.py`
  (`pydantic-settings`, all env), `models.py` (request/response models), `queue.py`
  (arq pool + enqueue). Knows nothing about rendering or Gemini internals.
- **`worker/`** — orchestration + intelligence. `worker.py` (arq entrypoint),
  `pipeline.py` (steps 1–5, status writes), `gemini_client.py` (every Gemini call,
  tenacity backoff, usage logging, stub fallback), `spec.py` (`SceneSpec` schema +
  validators), `refine.py` (passthrough hook).
- **`render/`** — pure, deterministic, no I/O beyond temp frames + ffmpeg. `primitives.py`
  (one function per drawable), `engine.py` (`SceneSpec` → frames → MP4), `palette.py`,
  `fonts.py`. Every primitive takes stroke/typography args so a future StyleProfile is
  config, not new code.

Each unit is independently testable: render needs no Redis/Gemini; the API needs no
worker (enqueue is mocked); Gemini client needs no network (respx).

## 5. SceneSpec contract (Phase 3)

`Scene.type ∈ {hook_claim, hook_question, pattern_interrupt, title_writeon,
boxes_popin, object_hop, arrow_flow, celebration, end_card, camera_pan,
custom_sprite_path}`; `duration_s ∈ (0, 8]`; `params` validated per-type via a
**discriminated union** (`TitleParams`, `BoxesParams`, `HopParams`, …).

`SceneSpec`: `title`, `fps ∈ {24,30}` (default 30), `aspect ∈ {16:9,9:16,1:1}`,
`resolution` derived from `ASPECT_PRESETS` (not LLM-chosen), `palette_override?`,
`scenes` (2–12), `total_duration_s ≤ 60`.

Validators enforced in Pydantic, never left to the LLM:
- **Hook enforcement** — `scenes[0].type` must be a hook type with `duration_s ≤ 3.0`.
- **Duration fidelity** — `total_duration_s` within 10% of the user's `duration_s` target.
- **Safe zones** — 9:16 forbids text in top 12% / bottom 18%; engine clamps placement.

## 6. Render engine (Phase 2)

PIL draws jittered "hand-drawn" strokes (numpy-vectorised jitter), seeded per scene
index for determinism. Frames written as numbered PNGs to a temp dir, encoded with:
`ffmpeg -framerate {fps} -i f%05d.png -c:v libx264 -pix_fmt yuv420p -crf 20 out.mp4`.
Temp frames deleted after encode. Target: < 60s wall for a 15s 720p clip
(ProcessPoolExecutor across scenes if profiling demands).

## 7. API contract (Phase 5)

- `POST /generate` — multipart: `prompt` (≤2000), `image?` (≤10 MB), `duration_s`
  (5–60), `aspect` (16:9|9:16|1:1), `captions` (default true), `draft` (default false).
  → `{job_id, status:"queued"}`.
- `GET /jobs/{id}` → `{status, progress_pct, error?}`.
- `GET /jobs/{id}/video` → streams MP4 when done, 404 otherwise.
- `GET /jobs/{id}/spec` → compiled `SceneSpec` (debug).
- `GET /health` → `{status:"ok"}`. `GET /` → static UI.
- Limits: image ≤10 MB, prompt ≤2000, per-IP rate limit (`slowapi`).

## 8. Gemini integration (Phase 4, mocked)

Unified `google-genai` SDK. Model IDs from config, never hardcoded:
`GEMINI_VISION_MODEL`, `GEMINI_SPEC_MODEL`, `GEMINI_SPEC_MODEL_FALLBACK`. Temp 0.3 for
spec compile, 0.7 for refine. Retry: 2 retries appending validation errors → escalate
to fallback pro model → mark job failed with a clean payload. `tenacity` exponential
backoff on 429/5xx. Log `usage_metadata` per call. **Never log image bytes** — only the
`ImageBrief`. Reject real public figures / copyrighted characters at refine → 422.

## 9. Testing & guardrails

- Unit test per primitive: render 3 frames, assert no exception + non-blank pixels.
- Contract test: every `Scene.type` has exactly one engine handler (fail CI otherwise).
- Golden-frame test: `imagehash` frame 0 + frame N of the demo spec catches regressions.
- Schema tests: 3 valid + 5 invalid fixtures raise precise field-level errors.
- API tests: `httpx` ASGITransport; enqueue mocked.
- Gemini tests: `respx` mocks; one opt-in `@pytest.mark.live` for real-key runs.

## 10. Build phases (this session)

1. **Skeleton** — repo, `pyproject` (uv), docker-compose (api/worker/redis), `/health`,
   static UI shell, dummy job round-trips arq. *Verify before continuing.*
2. **Render library** — primitives + engine + all scene types; demo spec renders a
   playable MP4; primitive tests pass.
3. **Schema** — `SceneSpec` discriminated unions + validators; golden fixtures.
4. **Gemini (mocked)** — `gemini_client` vision + compile, retry/fallback, usage logging;
   respx tests; stub fallback when no key.
5. **Pipeline + API** — wire steps 1–5 with status updates; all endpoints; UI end-to-end
   produces an MP4 (Gemini stubbed).

**Deferred:** Phase 6 (TTS/webhooks), 7 (overlay mode), 8 (style learning),
9 (integration hardening), 10 (brand kits/captions/marketing). Tracked in `product-plan.md`.

## 11. Environment variables

```
GEMINI_API_KEY=                       # absent -> stub spec compiler
GEMINI_VISION_MODEL=gemini-2.5-flash
GEMINI_SPEC_MODEL=gemini-2.5-flash
GEMINI_SPEC_MODEL_FALLBACK=gemini-2.5-pro
REDIS_URL=redis://redis:6379
OUTPUT_DIR=/data/outputs
MAX_PROMPT_CHARS=2000
MAX_UPLOAD_IMAGE_MB=10
```
