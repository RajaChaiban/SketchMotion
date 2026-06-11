# ✎ SketchMotion

Turn a **prompt, a picture, or a video** into a **hand-drawn sketch** — an animated video or
a single still. You pick a few options; an **AI agent analyzes the request and routes it** to
the right pipeline; a deterministic Python engine renders the output.

> **Core principle: the LLM plans, Python draws.** The AI never renders pixels — it emits a
> validated JSON scene plan (or a routing decision), and PIL + ffmpeg produce the result. That
> separation makes output reproducible and the whole thing testable.

- **Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)
- **Project instructions (for AI coding agents):** [`CLAUDE.md`](CLAUDE.md)
- **Design specs & roadmap:** [`docs/`](docs/)

---

## What it does

One intake (`POST /create`), three routes the agent chooses between:

| You give it | + option | → Route | Output |
|---|---|---|---|
| a prompt | — | **animate** | hand-drawn **video** |
| a prompt + a picture | — | **animate** (picture as reference) | video |
| a picture | "still" | **stylize_image** | sketched **still (PNG)** |
| a video | — | **stylize_video** | sketched **video** (audio kept) |

You select **mode** (auto / animate / stylize), **output** (auto / video / still), **sketch
style** (auto / ink / pencil), and for animations a **duration** and **format** (16:9 / 9:16 /
1:1). In `auto`, the agent reads your inputs and decides the route *and* a content-appropriate
style, with a one-line reason.

**Sketch styles:** **ink** (bold outlines + flattened color on paper — great for action) and
**pencil** (graphite color-dodge — great for portraits).

---

## Quick start

### Docker (everything)

```bash
cp .env.example .env          # GEMINI_API_KEY can stay empty
docker compose up --build     # UI → http://localhost:8000
```

### Local (no Docker)

```bash
uv sync
docker run -d --rm -p 6379:6379 redis:7-alpine     # any Redis works
uv run uvicorn app.main:app --reload --port 8000   # API + UI
uv run arq worker.worker.WorkerSettings            # worker (separate terminal)
```

Open http://localhost:8000, use the **Create** card, and watch it build.

---

## No API key required

The "AI agent" is a pluggable provider — set with `LLM_PROVIDER`:

| `LLM_PROVIDER` | Backend | Needs | When |
|---|---|---|---|
| `gemini` | google-genai | `GEMINI_API_KEY` | production |
| `claude_cli` | your local `claude` CLI | Claude Code on PATH | dev — real prompt-aware specs, **no key** |
| `stub` | deterministic templates | nothing | CI / offline |
| `auto` *(default)* | gemini → claude_cli → stub | — | picks the best available |

Going to production is one env var (`LLM_PROVIDER=gemini` + key) — **no code change**.

---

## CLI (no server needed)

```bash
# Render a scene spec → MP4
uv run python -m render.engine demo_spec.json out.mp4

# Sketch a real video (audio preserved)
uv run python -m worker.stylize in.mp4 out.mp4 --style ink      # or pencil
uv run python -m worker.stylize in.mp4 out.mp4 --style pencil --seconds 12 --workers 8
```

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/create` | unified intake — options in, agent routes, returns `{job_id, route, output_kind, style}` |
| POST | `/generate` | direct: prompt (+ image) → animated video |
| POST | `/stylize` | direct: video → sketched video |
| GET | `/jobs/{id}` | status `{status, progress_pct, route, output_kind, error?}` |
| GET | `/jobs/{id}/video` · `/image` | the finished MP4 / PNG |
| GET | `/jobs/{id}/spec` | the compiled `SceneSpec` (debug) |
| GET | `/health` · `/` | liveness · web UI |

---

## How a request flows

```
prompt / picture / video + options
        │  POST /create
        ▼
  mode=auto ─▶ intake_job ─▶ 🧠 router agent (LLM)  ─(safe fallback to rules)
        │                      → {route, style, output_kind} + reason
        ▼  dispatch
  animate · stylize_video · stylize_image     (each shaped by its SKILL)
        ▼
  /jobs/{id}/video   or   /jobs/{id}/image
```

- **animate**: refine → (vision on the image) → compile a validated `SceneSpec` → render.
- **stylize**: extract frames → sketch each → re-encode with the original audio.

Each pipeline has a **skill** (`skills/prompt-refiner`, `skills/video-stylist`) that guides
the agent's decisions (scene archetypes & pacing; style choice & filter tuning).

---

## Scene vocabulary

The animation engine draws from a fixed set of hand-drawn scene types (13): opening **hooks**,
`title_writeon`, `boxes_popin`, `arrow_flow`, `object_hop`, `camera_pan`, `custom_sprite_path`,
`celebration`, `end_card`, and sports scenes **`basketball_tip`** (shot → clank off the rim →
tip-in → swish) and **`scoreboard`**. Every scene is validated against a strict schema (hook
first, durations sum to target, safe-zone text) before a single frame is drawn.

---

## Testing

```bash
uv run pytest                 # 153 tests — no network; ffmpeg-gated tests skip if ffmpeg absent
uv run pytest -m claude_cli   # opt-in: real local claude CLI (set RUN_CLAUDE_CLI=1)
uv run pytest -m live         # opt-in: real Gemini API (needs GEMINI_API_KEY)
```

The suite mocks the LLM at the provider boundary (no HTTP), guarantees every scene type has a
renderer, checks render determinism, verifies the router's safe fallbacks, and confirms audio
survives stylization.

---

## Status & roadmap

**Built:** generate (prompt → video), stylize (video/image → sketch), the unified `/create`
intake, the LLM auto-router, per-pipeline skills, and still-image output.

**Deferred** (see [`docs/product-plan.md`](docs/product-plan.md)): overlay-mode **annotations**
(detect key moments → draw arrows/callouts on stylized footage), **style learning** (distill a
user's old animations into a reusable style), brand kits, and TTS narration.

## Notes

- **Stylization is fast** now: frames stream through ffmpeg in memory (no PNG/disk), sketch
  across threads, and cap at 30fps — a 42s 720p/60fps clip went **~16min → ~3min**. Trim with
  `--seconds N` for an even quicker highlight.
- Downloaded source clips and full stylized outputs are git-ignored — keep third-party footage
  local and respect its rights.
