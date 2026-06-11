# SketchMotion — Full Product Plan (source of record)

This is the complete multi-mode vision. The current build session implements **Phases 1–5**
(generate mode + static UI) per [`specs/2026-06-11-sketchmotion-design.md`](specs/2026-06-11-sketchmotion-design.md).
Everything below the line is **deferred** and tracked here so it isn't lost.

## Three modes

1. **Generate** — text prompt + optional image → compiled `SceneSpec` → rendered MP4. *(building now)*
2. **Overlay** — user uploads a finished video → Gemini detects key moments → composite
   sketch annotations (arrows, callouts, highlights, write-on captions) on top, audio preserved.
3. **Style learning** — user uploads a library of their old animations → background job
   distills a reusable `StyleProfile` and auto-writes a style skill → applied to later jobs.

---

## Deferred phases

- **Phase 6 — Polish:** Kokoro TTS narration (Gemini script → ffmpeg mux), completion webhook,
  presigned S3 delivery, structlog, per-job cost report.
- **Phase 7 — Overlay mode:** `video_ingest` (ffprobe, frame sampling, audio split),
  `moment_detect` (Gemini Files API video understanding → timestamped moments),
  `OverlaySpec` schema (≤40 annotations, ≤30% frame coverage), `overlay_engine` (RGBA frames),
  ffmpeg overlay compositing. Endpoints `POST /overlay`, `GET /jobs/{id}/moments`.
- **Phase 8 — Style learning:** `style_analyze` (per-video Gemini), `style_distill`
  (aggregate → `StyleProfile`), `skill_writer` (Jinja → `SKILL.md` + few-shots), validation
  render, immutable versioning. Endpoints `POST/GET/DELETE /styles`, `POST /styles/{id}/approve`.
- **Phase 9 — Integration hardening:** 3-mode E2E matrix, cost reports, 7-day cleanup of
  uploaded sources.
- **Phase 10 — Marketing layer:** aspect presets + safe zones (partly in Phase 2/3),
  `BrandKit` with precedence over StyleProfile, captions in both modes (SceneSpec caption
  field for generate; Gemini audio transcription for overlay), `.srt` sidecar, draft preview,
  free-tier watermark.

## Schemas defined for deferred phases

- `OverlaySpec` / `Annotation` — timestamped normalized-anchor annotations.
- `StyleProfile` — palette, stroke, typography, pacing, motion_vocab, motifs, tone, confidence.
- `BrandKit` — logo, exact palette, licensed fonts, watermark corner, tagline. Brand kit wins
  over learned style on conflict.

## Guardrails (all modes)

- Ownership confirmation required for uploaded videos / style libraries; reject obvious
  third-party commercial content.
- Cap analysis at 10 min of video; downsample style refs to 480p / 1 fps.
- Reject real public figures / copyrighted characters at the refine step (422).
- Never log raw image/video bytes — only derived structured briefs.

## Full dependency set (eventual)

fastapi, uvicorn[standard], python-multipart, pydantic, pydantic-settings, slowapi, arq,
redis, google-genai, tenacity, pillow, numpy, fonttools, scenedetect, filetype, pysubs2,
jinja2, imagehash, aiofiles, boto3, structlog, prometheus-client. Dev: pytest, pytest-asyncio,
httpx, respx. TTS: kokoro, soundfile.
