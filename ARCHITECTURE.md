# SketchMotion — Architecture

> **The LLM plans, Python draws.** The AI agent never writes render code or pixels. It emits
> a *validated JSON plan* (`SceneSpec`) or a *routing decision*; a deterministic PIL + ffmpeg
> engine produces the output. This boundary is why results are reproducible and testable.

---

## 1. System overview

```
                          Browser (static page, app/static/index.html)
                          prompt / picture / video + options
                                   │  POST /create (multipart)
                                   ▼
   ┌──────────────────────── FastAPI (app/) ────────────────────────┐
   │  /create  · /generate · /stylize     (validate + persist upload) │
   │  /jobs/{id}[/video|/image|/spec]      (status + serve output)     │
   │  returns {job_id} immediately                                    │
   └───────────────────────────────┬─────────────────────────────────┘
                                    │ enqueue (arq)
                                    ▼
                          Redis  ── job queue + per-job status/meta hash
                                    │
                                    ▼
   ┌──────────────────────── Worker (worker/) ───────────────────────┐
   │ intake_job ─▶ router.decide() [LLM]  ─▶ dispatch:                 │
   │   • generate_job → run_generate (refine→vision→compile→render)   │
   │   • stylize_job  → stylize_video (ingest→sketch frames→encode)   │
   │   • stylize_image_job → stylize_image (one frame)                │
   └───────────────────────────────┬─────────────────────────────────┘
                                    ▼
                OUTPUT_DIR/{job_id}.mp4  or  {job_id}.png
```

Three processes: **API** (FastAPI/uvicorn, also serves the UI), **worker** (arq), **Redis**.
`docker compose up` runs all three.

---

## 2. Layers & responsibilities

| Layer | Package | Knows about | Does NOT know about |
|---|---|---|---|
| HTTP | `app/` | requests, options, the queue | rendering, LLM internals |
| Orchestration | `worker/{worker,pipeline,intake,router}.py` | job lifecycle, routing, step order | how a stroke is drawn |
| Intelligence | `worker/{llm,gemini_client,refine,skills}.py` | LLM calls, validation, skills | frame buffers |
| Rendering | `render/` | PIL, ffmpeg, geometry | Redis, HTTP, LLM |

Each layer is independently testable: rendering needs no Redis/LLM; the API needs no worker
(enqueue is faked); the LLM layer needs no network (mocked at the provider boundary).

---

## 3. The three pipelines

### 3.1 Animate (prompt → sketch video) — `worker/pipeline.py::run_generate`
1. **Vision** *(if an image is attached and the provider supports it)* → `ImageBrief`
   (subject, objects, palette, mood, suggested sprites).
2. **Refine** (`worker/refine.py`) — passthrough hook + content screen (rejects protected
   IP → clean failure). The `prompt-refiner` **skill** text is loaded here/at compile.
3. **Compile** — `provider.compile_spec()` returns a `SceneSpec`. Structured output is
   re-validated with Pydantic; on failure the error is appended and retried (2×), then the
   call escalates to a fallback model (Gemini), then fails cleanly.
4. **Render** — `render/engine.py::render_spec(spec.model_dump(), out)`.

Status lifecycle: `queued → analyzing? → vision → refining → compiling → rendering → done|failed`.

### 3.2 Stylize video (real video → sketch video) — `worker/stylize.py::stylize_video`
`ingest (ffprobe + extract frames) → sketchify each frame → re-encode`. Original **audio is
preserved** (`ffmpeg -map 1:a:0 -shortest`). `--workers` parallelizes frames across processes.

### 3.3 Stylize image (image → sketch still) — `worker/stylize.py::stylize_image`
One frame through the same `sketchify` filter → PNG, served at `/jobs/{id}/image`.

---

## 4. Ingestion & the auto-router

`POST /create` builds an intake request (prompt + sniffed file + options) and:
- **Explicit mode** (`animate`/`stylize`) → deterministic `intake.route()` picks the job and
  enqueues it directly (fast, no LLM).
- **`mode=auto`** → enqueues `intake_job`, which runs the **router agent** in the worker:

```
router.decide(provider, prompt, file_kind, image_summary, options):
  base = intake.route(...)                      # always a valid deterministic baseline
  text = provider.analyze(routing_prompt)       # LLM classifies route/style/output_kind
  if no text / unparseable / infeasible-vs-attachment:
      return base                               # safe fallback — agent can only refine
  return validated decision (route + content-aware style + reason)
```

The decision is recorded to the job hash (`route`, `output_kind`) so the UI loads
**video-or-still** from the live status, not a guess. **Invariant:** the agent never expands
capability beyond what's attached — it can't pick `stylize_video` without a video.

---

## 5. The `SceneSpec` contract (`worker/spec.py`)

The single source of truth between LLM and renderer.

```
SceneSpec: title, fps∈{24,30}, aspect∈{16:9,9:16,1:1}, resolution (derived from aspect,
           not LLM-chosen), palette_override?, scenes[2..12], total_duration_s≤60
Scene:     type (13 literals), duration_s∈(0,8], params (per-type model), caption?
```

Validators enforced in Pydantic, never left to the LLM:
- **Hook first** — `scenes[0].type` ∈ {hook_claim, hook_question, pattern_interrupt}, ≤3s.
- **Duration fidelity** — `total_duration_s` == sum of scene durations, and within ±10% of the
  user's target (passed via validation context).
- **Per-type params** — a discriminated set of models (`TextParams`, `BoxesParams`,
  `BasketballTipParams`, `ScoreboardParams`, …) validated by `Scene.type`.
- **Safe zones / hex palette** — 9:16 keeps text out of platform chrome; palette must be hex.

`validate_spec(data, target_duration_s)` is the one entrypoint; `stub_compile()` and every
provider funnel through it.

---

## 6. Render engine (`render/`)

- **`primitives.py`** — seeded jitter, `sketch_line/rect/ellipse/arrow`, `write_on_lines`,
  sprites (ball/star/rocket/bulb/heart/trophy/stick_figure), **basketball/hoop**, confetti,
  easings. Every drawable takes stroke/typography args (so a future StyleProfile is config,
  not new code).
- **`engine.py`** — `SCENE_HANDLERS` maps each `Scene.type` → one handler. `render_frame`
  builds a paper canvas, seeds RNG per `(scene, frame)`, draws, then a safe-zone caption.
  `render_spec` writes numbered PNGs → `ffmpeg -c:v libx264 -pix_fmt yuv420p`. `clean_text`
  sanitizes unrenderable glyphs. Determinism makes golden-frame hashing possible.
- **`sketch_filter.py`** — `sketchify(img, style)`: **ink** (FIND_EDGES → ink mask over
  posterized, paper-tinted color) and **pencil** (grayscale color-dodge on paper). Pure and
  stateless → parallelizes across frames.

**Scene catalog (13):** hooks (3), `title_writeon`, `boxes_popin`, `object_hop`,
`arrow_flow`, `celebration`, `end_card`, `camera_pan`, `custom_sprite_path`,
`basketball_tip` (shot arc → clank → tipper leaps → swish), `scoreboard`.

---

## 7. LLM provider layer (`worker/llm.py`)

One interface, three backends, one factory.

```
LLMProvider:  vision(image) -> ImageBrief | None
              compile_spec(prompt, target, aspect, image_brief, skill) -> SceneSpec
              analyze(prompt) -> str | None        # routing/classification
```

- **GeminiProvider** — `google-genai`; structured output + retry/fallback; production.
- **ClaudeCliProvider** — shells `claude -p … --output-format json`; real prompt-aware specs
  with **no API key** (dev). Slow (~10–120s) and token-costed → worker-only.
- **StubProvider** — deterministic, offline templates; CI / no-key fallback.

`get_provider(settings)` resolves `LLM_PROVIDER` (`auto` → gemini → claude_cli → stub).
Switching backends is configuration, not code.

### Skills
A skill = `skills/<name>/SKILL.md` loaded by `worker/skills.py`. `prompt-refiner` is injected
into the compile prompt (archetype/pacing/hook rules + a few-shot spec); `video-stylist`
backs `STYLE_PRESETS` (filter params) and `choose_style()` (content → ink/pencil).

---

## 8. Job queue & state (`app/queue.py`)

`JobQueue` wraps an arq Redis pool. `enqueue(function, payload, job_id)` (generic, used by
`/create`) plus typed helpers; status/meta live in a Redis hash `sm:job:{id}` with
`status`, `progress_pct`, `error`, `route`, `output_kind`, `spec`. The API seeds `queued`;
the worker advances it. Tests inject a `FakeQueue` via `dependency_overrides` — no Redis.

---

## 9. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/` , `/health` | UI · liveness |
| POST | `/create` | unified intake (options → agent/route → job) |
| POST | `/generate` | direct animate |
| POST | `/stylize` | direct video stylize |
| GET | `/jobs/{id}` | status (+ `route`, `output_kind`) |
| GET | `/jobs/{id}/video` · `/image` | output (MP4 / PNG) |
| GET | `/jobs/{id}/spec` | compiled `SceneSpec` (debug) |

---

## 10. Testing

153 tests, **no network by default**. LLM mocked at the provider boundary
(`_raw_generate` / `_run_cli` / fakes). ffmpeg-touching tests skip if ffmpeg is absent.
Key guarantees: every `Scene.type` has a handler (contract); invalid specs raise precise
field errors; render is deterministic; the router falls back safely; `/create` routes per
input+option; audio survives stylization. Opt-in live markers: `live` (Gemini), `claude_cli`.

---

## 11. Extension points

- **New scene type** → add the `Scene.type` literal + param model (`worker/spec.py`), a
  handler (`render/engine.py::SCENE_HANDLERS`), update the contract test, and (optionally) the
  compile catalog so the LLM can pick it.
- **New sketch style** → add to `STYLE_PRESETS` + a `sketch_filter` branch + the
  `video-stylist` skill.
- **New route/modality** → extend `intake.route()` + `router` literals + a worker job +
  `/create` dispatch.
- **Production LLM** → set `LLM_PROVIDER=gemini` + `GEMINI_API_KEY`. No code change.

---

## 12. Deferred (see `docs/product-plan.md`)

Overlay-mode **annotations** (LLM detects key moments → composite arrows/callouts on
stylized frames), style-learning (distill a user's old animations into a reusable style
skill), brand kits, TTS narration, and **video-stylization performance** (in-memory ffmpeg
pipe to replace per-frame PNG extraction — current bottleneck).
