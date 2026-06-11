# Unified Intake + Per-Pipeline Skills — Plan

**Date:** 2026-06-11
**Goal:** One intake where the user picks options (input, output kind, style) up front; an
AI agent analyzes the request and routes it to the right pipeline. Each pipeline gets a
**skill** (markdown rules + structured presets) that shapes how it behaves.

## 1. Three routes (one intake)

| Route | Input | Output | Engine |
|---|---|---|---|
| `animate` | prompt (+ optional reference image) | sketch **video** | LLM → SceneSpec → render |
| `stylize_video` | a real video | sketch **video** | per-frame sketch filter |
| `stylize_image` | a single image | sketch **still** (PNG) | one-frame sketch filter |

## 2. Ingestion layer (the keystone)

- **`IntakeRequest`** (`app/models.py`): `prompt?`, uploaded `file?` (image|video, sniffed
  by magic bytes via `filetype`), and user options:
  `mode` (`auto|animate|stylize`), `output_kind` (`video|still`), `style`
  (`ink|pencil|auto`), `duration_s`, `aspect`.
- **`worker/intake.py::route(req) -> Plan`** — deterministic routing that **honors explicit
  options**; `mode=auto` decides from what's present (video→stylize_video, image+prompt→
  animate, image alone + still→stylize_image, prompt→animate). `Plan` = `{route,
  job_function, output_kind, payload}`. Hook left for an LLM-assisted auto decision.
- **`POST /create`** (multipart) — the single entrypoint. Builds the request, routes,
  persists the upload, enqueues the chosen job, returns `{job_id, status, route,
  output_kind}`. Existing `/generate` + `/stylize` stay for back-compat.
- New **`stylize_image_job`** + `GET /jobs/{id}/image` for still output.

## 3. A skill per pipeline

A *skill* = `skills/<name>/SKILL.md` (human-readable rules + few-shots) loaded by
`worker/skills.py::load_skill(name)` and injected where the agent makes decisions.

- **`prompt-refiner`** (animate pipeline): how to clarify vague prompts, choose scene
  archetypes, pacing, and few-shot `SceneSpec` examples → injected into the spec-compile
  prompt. Wires through `worker/refine.py` + `provider.compile_spec(skill=...)`.
- **`video-stylist`** (stylize pipeline): how to pick a sketch style for the content +
  per-style parameter presets (`STYLE_PRESETS`: edge strength, posterize bits, blur).
  Drives the `style=auto` chooser and the filter parameters.

## 4. Options-first UI

One card: input (prompt + file), **output** toggle (Animated video / Stylize video /
Sketch a still), **style** dropdown, and animate-only options (duration, aspect). Submits
to `/create`, polls `/jobs/{id}`, then loads `/video` or `/image` per `output_kind`.

## 5. Build order (this work)

1. Skills: both `SKILL.md` + `worker/skills.py` loader; wire `prompt-refiner` into compile.
2. Ingestion: `IntakeRequest`, `worker/intake.py`, `POST /create`, `stylize_image` +
   `stylize_image_job` + `/jobs/{id}/image`.
3. UI: unified options card.
4. Tests at each step (routing matrix, skill load+inject, /create per input/option, still
   output). Back-compat: `/generate` and `/stylize` unchanged.
