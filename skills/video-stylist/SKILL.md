# Skill: video-stylist (stylize pipeline)

Guidance for turning a real video (or image) into a hand-drawn sketch. Drives the
`style=auto` chooser and the per-style filter parameters (`STYLE_PRESETS` in
`worker/skills.py`).

## Styles
- **ink** — bold outlines over flattened, paper-tinted color. Best default: keeps subjects
  recognizable, reads well on busy/action footage (sports, crowds, product shots).
- **pencil** — grayscale color-dodge graphite. Best for portraits, calm scenes, a softer
  "hand-sketched" feel, or when color distracts.

## Choosing a style (when the user picks "auto")
- Fast motion / lots of color / sports / crowds → **ink**.
- Single subject / face / muted palette / nostalgic tone → **pencil**.
- Unsure → **ink** (the safer, more legible default).

## Parameter intent (tuning the look)
- `edge_strength` ↑ = heavier ink lines (bolder, busier). Lower for clean footage.
- `posterize_bits` ↓ = flatter, more cartoon-like color. Higher keeps more gradient.
- `color_mix` ↓ = washes color toward paper (softer, more "drawn"). Higher stays vivid.
- `blur_radius` (pencil) ↑ = softer, broader graphite shading.

## Guardrails
- Preserve the original audio.
- Cap analysis at the configured max seconds; for longer sources, ask the user to trim.
- Only stylize footage the user has the right to use.
