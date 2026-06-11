# SketchMotion

Prompt / picture / video **in** → hand-drawn sketch **out**. An AI agent analyzes the
request and routes it to one of three pipelines; a deterministic Python engine renders the
result. **Core principle: the LLM plans, Python draws.** The LLM never renders pixels — it
emits a Pydantic-validated `SceneSpec` (or a routing decision), and PIL+ffmpeg do the rest.

Design + roadmap: `docs/specs/` and `docs/product-plan.md`. Deep architecture: `ARCHITECTURE.md`.

## Stack (verified)

Backend = Python **3.12**, FastAPI, **arq** + Redis (async job queue + status), Pydantic 2 +
pydantic-settings, **Pillow** + **numpy** (rendering), **ffmpeg** (system binary, via
subprocess), **google-genai** + tenacity (LLM), slowapi (rate limit), filetype (upload
sniffing), aiofiles. Tooling = **uv**, pytest (`asyncio_mode=auto`). Frontend = a single
static page (`app/static/index.html`, vanilla JS) served by FastAPI — **no Node/React**.

## Run

```bash
# Docker (everything): UI at http://localhost:8000
cp .env.example .env            # GEMINI_API_KEY may stay empty
docker compose up --build

# Local (3 terminals)
uv sync
docker run -d --rm -p 6379:6379 redis:7-alpine        # or any Redis
uv run uvicorn app.main:app --reload --port 8000       # API + UI
uv run arq worker.worker.WorkerSettings                # worker

# Tests / checks
uv run pytest                                          # 153 tests; no network, ffmpeg-gated ones skip if absent
uv run pytest -m claude_cli   # opt-in: hits the real claude CLI (RUN_CLAUDE_CLI=1)
uv run pytest -m live         # opt-in: hits the real Gemini API (needs GEMINI_API_KEY)

# Render a spec directly / stylize a file directly (no server)
uv run python -m render.engine demo_spec.json out.mp4
uv run python -m worker.stylize in.mp4 out.mp4 --style ink|pencil [--seconds N] [--workers 8]
```

## LLM provider (the "AI agent") — `LLM_PROVIDER`

`auto` (default) | `gemini` | `claude_cli` | `stub`. `auto` = gemini if `GEMINI_API_KEY`
set, else the local `claude` CLI if on PATH, else stub. **Everything works with no key**
(stub = deterministic templates; claude_cli = real prompt-aware specs via the local CLI).
Switching to production is one env var (`LLM_PROVIDER=gemini` + key) — **no code change**.
The provider does three jobs behind one interface (`worker/llm.py`): `vision`, `compile_spec`,
`analyze` (routing).

## Conventions (hard rules — the user was burned by violations)

- **Validate every LLM output with Pydantic, even with structured output.** `compile_spec`
  re-runs `validate_spec()` and retries with the error appended; the router validates the
  decision against what's actually attached and **falls back to deterministic rules** on any
  failure. The agent can only *refine* a safe decision, never break the run.
- **Rendering is deterministic.** Jitter is seeded per `(scene_index, frame_index)`; same
  spec → identical frames (golden-frame tests depend on this). Don't introduce
  `random`/`time`-based drawing.
- **ASCII-only text in specs; the engine sanitizes anyway.** Models love `→`/`↑`/smart
  quotes the sketch font can't draw — `render/engine.py::clean_text` maps them to ASCII at
  the render boundary. Keep that net; don't remove it.
- **Decode subprocess output as UTF-8.** Windows defaults to cp1252 and crashes on
  em-dashes/arrows (`UnicodeDecodeError` → `stdout=None` → `TypeError`). All ffmpeg/ffprobe/
  claude subprocess calls pass `encoding="utf-8", errors="replace"`.
- **Original audio is preserved** through stylization (ffmpeg `-map 1:a:0 -shortest`). Never
  drop it.
- **Every `Scene.type` has exactly one engine handler.** A contract test
  (`tests/test_engine.py`) fails if a type is added to the schema without a handler. Add both.
- **No network in the default test suite.** LLM is mocked at the provider/`_raw_generate`/
  `_run_cli` boundary or via fakes (not HTTP mocks). ffmpeg-touching tests skip when ffmpeg is
  absent. Live calls live behind `@pytest.mark.live` / `claude_cli`.
- **Fix → test → proceed.** Every change runs `uv run pytest` (add/extend a test for new
  behavior) before moving on; ship on a branch, not `main`.

## Three routes (one intake)

| Route | Input | Output | Engine |
|---|---|---|---|
| `animate` | prompt (+ optional reference image) | sketch **video** | LLM → `SceneSpec` → render |
| `stylize_video` | a real video | sketch **video** | per-frame sketch filter |
| `stylize_image` | a single image | sketch **still** (PNG) | one-frame sketch filter |

`POST /create` is the unified intake: user picks options (`mode` auto/animate/stylize,
`output_kind`, `style` ink/pencil/auto, duration, aspect). `mode=auto` → `intake_job` runs
the **router agent** (LLM) in the worker, then dispatches inline. Explicit modes route
directly. `/generate` and `/stylize` remain as direct entrypoints.

## Where things live

- **API (`app/`):** `main.py` (routes + static mount + `/create` routing), `config.py`
  (pydantic-settings, all env), `models.py` (request/response), `queue.py` (`JobQueue`: arq
  enqueue + Redis status/meta hash). Knows nothing about rendering/LLM internals.
- **Worker (`worker/`):** `worker.py` (arq jobs: `generate_job`, `stylize_job`,
  `stylize_image_job`, `intake_job` + `WorkerSettings`), `pipeline.py` (`run_generate`:
  refine→vision→compile→render), `intake.py` (deterministic `route()`), `router.py` (LLM
  `decide()` + safe fallback), `llm.py` (provider layer + factory), `gemini_client.py`
  (Gemini calls + `stub_compile`), `spec.py` (`SceneSpec` + validators), `refine.py`
  (passthrough + content screen), `skills.py` (skill loader + style presets),
  `stylize.py` (`stylize_video`/`stylize_image`), `video_ingest.py` (ffprobe + frame extract).
- **Render (`render/`):** `primitives.py` (jitter strokes, sprites, basketball/hoop,
  confetti, easings), `engine.py` (`SceneSpec` dict → frames → ffmpeg; `SCENE_HANDLERS`,
  `clean_text`, safe-zone captions), `sketch_filter.py` (`sketchify` ink/pencil),
  `palette.py`, `fonts.py`. Pure, deterministic, no Redis/LLM.
- **Skills (`skills/`):** `prompt-refiner/SKILL.md` (animate), `video-stylist/SKILL.md`
  (stylize). Loaded by `worker/skills.py`; injected where the agent decides.

## Scene types (13) & styles

`hook_claim` · `hook_question` · `pattern_interrupt` · `title_writeon` · `boxes_popin` ·
`object_hop` · `arrow_flow` · `celebration` · `end_card` · `camera_pan` ·
`custom_sprite_path` · `basketball_tip` · `scoreboard`. Sketch-filter styles: `ink`
(outlines + flat color) · `pencil` (graphite color-dodge).

## Known gotchas / failure modes

- **Stylization** streams raw RGB frames between two ffmpeg subprocesses (no PNG/disk),
  sketches across **threads** (PIL/numpy release the GIL — process pools lose to per-frame
  IPC here), and caps output at 30fps (`max_fps`). A 42s 720p/60fps clip went 16min → ~3min.
  The filter itself is tuned (numpy 3x3 dilation instead of PIL `MaxFilter`; in-place 0..255
  blend). Don't reintroduce per-frame PNG round-trips or a `ProcessPoolExecutor` over frames.
- **`claude_cli` is slow (~10–120s/call) and costs tokens.** It runs in the worker (a job),
  never synchronously in a request. Don't call it from a request handler.
- **Stop the backend before heavy local work isn't needed here** (Redis is local/disposable),
  but **kill stray uvicorn/worker before re-running** so old code isn't served.
- **Copyright:** downloaded source clips and full stylized outputs are git-ignored
  (`outputs/`, `*.mp4`). Keep third-party footage local; don't commit or republish it.
- **Windows LF→CRLF git warnings are benign.**

## Coordination rule

A change to a route, its request/response shape, or a scene/param model **moves together**
with: (1) the schema (`worker/spec.py`), (2) the engine handler (`render/engine.py`), (3) the
contract/golden tests, and (4) `README.md`/`ARCHITECTURE.md` if the public surface changed.
Never let one drift from the others.
