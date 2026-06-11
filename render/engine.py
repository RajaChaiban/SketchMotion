"""SceneSpec -> PNG frame sequence -> ffmpeg-encoded MP4.

The engine consumes a plain dict (the Pydantic SceneSpec's ``model_dump()`` in Phase 5,
or a hand-written demo spec). Each scene ``type`` maps to exactly one handler in
``SCENE_HANDLERS`` — the contract test in ``tests/test_engine.py`` fails CI if a type
is added without a handler. Rendering is deterministic: jitter is seeded per
(scene index, frame index).

CLI:  uv run python -m render.engine demo_spec.json [out.mp4]
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from render import primitives as P
from render.fonts import get_font
from render.palette import INK, PAPER, PALETTE, accent

ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (1080, 1080),
}

# Fraction of frame height reserved from text by aspect (top, bottom) — platform chrome.
SAFE_ZONES: dict[str, tuple[float, float]] = {
    "16:9": (0.04, 0.06),
    "9:16": (0.12, 0.18),
    "1:1": (0.06, 0.08),
}


# Map glyphs the hand-drawn font can't render to ASCII so text never shows tofu/mojibake.
_TEXT_REPL = {
    "→": "->", "←": "<-", "↑": "up", "↓": "down", "⇒": "=>",
    "–": "-", "—": "-", "•": "-", "…": "...",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}


def clean_text(s):
    if not isinstance(s, str):
        return s
    for k, v in _TEXT_REPL.items():
        s = s.replace(k, v)
    return s


def _clean_params(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, str):
            out[k] = clean_text(v)
        elif isinstance(v, list):
            out[k] = [clean_text(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


def fit_label(draw, text: str, max_w: float, max_h: float, base_px: float):
    """Largest font (and wrap) that fits `text` inside (max_w, max_h). Returns (font, lines, line_h)."""
    px = max(10.0, base_px)
    while px >= 10:
        font = get_font(int(px))
        lines = P.wrap_text(draw, text, font, max_w)
        _, lh = P.text_size(draw, "Ag", font)
        widest = max(P.text_size(draw, ln, font)[0] for ln in lines)
        if widest <= max_w and lh * len(lines) <= max_h:
            return font, lines, lh
        px -= 2
    font = get_font(10)
    lines = P.wrap_text(draw, text, font, max_w)
    _, lh = P.text_size(draw, "Ag", font)
    return font, lines, lh


def draw_label(draw, text: str, center, max_w: float, max_h: float, base_px: float, color=None):
    color = INK if color is None else color
    font, lines, lh = fit_label(draw, text, max_w, max_h, base_px)
    cx, cy = center
    y = cy - lh * len(lines) / 2
    for ln in lines:
        tw, _ = P.text_size(draw, ln, font)
        draw.text((cx - tw / 2, y), ln, font=font, fill=color)
        y += lh


@dataclass
class Frame:
    img: Image.Image
    draw: ImageDraw.ImageDraw
    t: float                       # 0..1 within the scene
    dur: float                     # scene duration in seconds
    size: tuple[int, int]
    palette: list[str]
    rng: Any                       # per-frame RandomState (boiling jitter)
    scene_seed: int                # stable per-scene seed (confetti init, etc.)
    params: dict

    def font(self, px: int):
        return get_font(max(8, int(px)))


# --- scene handlers ----------------------------------------------------------

def _title_text(params: dict) -> str:
    for key in ("text", "claim", "question", "title", "label"):
        v = params.get(key)
        if v:
            return str(v)
    return ""


def _h_text(f: Frame) -> None:
    text = _title_text(f.params) or "Sketch"
    w, h = f.size
    font = f.font(h * 0.11)
    lines = P.wrap_text(f.draw, text, font, w * 0.86)
    progress = P.ease_out_cubic(min(1.0, f.t / 0.6))
    P.write_on_lines(f.draw, (w / 2, h * 0.46), lines, font, f.palette[0], progress)
    # underline strokes in once text is mostly on
    if progress > 0.85:
        u = P.ease_out_cubic((progress - 0.85) / 0.15)
        y = h * 0.46 + h * 0.10 * len(lines) / 2 + h * 0.02
        P.sketch_line(f.draw, (w * 0.2, y), (w * 0.2 + (w * 0.6) * u, y), accent(1, f.palette), 5, f.rng, 2.5)


def _h_boxes(f: Frame) -> None:
    items = [str(x) for x in (f.params.get("items") or ["Idea", "Build", "Ship"])][:6]
    w, h = f.size
    n = len(items)
    cols = min(n, 3)
    rows = math.ceil(n / cols)
    cw = w * 0.8 / cols
    ch = min(h * 0.22, w * 0.8 / cols * 0.7)
    x_start = (w - cw * cols) / 2
    y_start = (h - ch * rows - (rows - 1) * ch * 0.3) / 2
    for i, label in enumerate(items):
        rdx, cdx = divmod(i, cols)
        appear = P.clamp01((f.t - i * 0.12) / 0.4)
        s = P.ease_out_back(appear)
        if s <= 0.01:
            continue
        bx = x_start + cdx * cw + cw * 0.08
        by = y_start + rdx * (ch * 1.3) + ch * 0.08
        bw = cw * 0.84 * s
        bh = ch * 0.84 * s
        cx = bx + cw * 0.42
        cy = by + ch * 0.42
        box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
        P.sketch_rect(f.draw, box, accent(i, f.palette), 4, f.rng, 2.5)
        if s > 0.85:
            draw_label(f.draw, label, (cx, cy), cw * 0.84 * 0.86, ch * 0.84 * 0.8, ch * 0.26)


def _h_object_hop(f: Frame) -> None:
    w, h = f.size
    name = f.params.get("sprite") or "ball"
    sz = min(w, h) * 0.16
    x = (0.12 + 0.76 * f.t) * w
    baseline = h * 0.62
    hops = int(f.params.get("hops", 3))
    y = baseline - abs(math.sin(f.t * hops * math.pi)) * h * 0.22
    P.sprite(f.draw, name, (x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2), accent(0, f.palette), f.rng)
    # ground line
    P.sketch_line(f.draw, (w * 0.08, baseline + sz / 2), (w * 0.92, baseline + sz / 2), INK, 3, f.rng, 2.0)


def _h_arrow_flow(f: Frame) -> None:
    steps = [str(x) for x in (f.params.get("steps") or f.params.get("items") or ["A", "B", "C"])][:4]
    w, h = f.size
    n = len(steps)
    cy = h * 0.5
    bw = w * 0.8 / n
    boxw = bw * 0.66
    boxh = min(h * 0.2, boxw * 0.7)
    centers = [(w * 0.1 + bw * (i + 0.5), cy) for i in range(n)]
    for i, (cx, _) in enumerate(centers):
        appear = P.clamp01((f.t - i * 0.18) / 0.3)
        s = P.ease_out_back(appear)
        if s <= 0.01:
            continue
        box = (cx - boxw / 2 * s, cy - boxh / 2 * s, cx + boxw / 2 * s, cy + boxh / 2 * s)
        P.sketch_rect(f.draw, box, accent(i, f.palette), 4, f.rng, 2.0)
        if s > 0.85:
            draw_label(f.draw, steps[i], (cx, cy), boxw * 0.86, boxh * 0.8, boxh * 0.3)
        if i > 0:
            ap = P.clamp01((f.t - (i - 0.5) * 0.18) / 0.25)
            if ap > 0:
                x_from = centers[i - 1][0] + boxw / 2
                x_to = centers[i][0] - boxw / 2
                P.sketch_arrow(f.draw, (x_from, cy), (x_from + (x_to - x_from) * ap, cy),
                               accent(1, f.palette), 4, f.rng, 2.0)


def _h_celebration(f: Frame) -> None:
    w, h = f.size
    parts = P.make_confetti(140, f.size, P.rng(f.scene_seed))
    snap = P.confetti_at(parts, f.t * f.dur)
    P.draw_confetti(f.draw, snap, f.palette)
    pop = P.ease_out_back(min(1.0, f.t / 0.5))
    tsz = min(w, h) * 0.34 * pop
    cx, cy = w / 2, h * 0.5
    if tsz > 4:
        P.trophy(f.draw, (cx - tsz / 2, cy - tsz / 2, cx + tsz / 2, cy + tsz / 2), accent(2, f.palette), f.rng, 5)
    label = f.params.get("label") or _title_text(f.params)
    if label and f.t > 0.4:
        font = f.font(h * 0.07)
        tw, _ = P.text_size(f.draw, str(label), font)
        f.draw.text((cx - tw / 2, cy + tsz / 2 + h * 0.02), str(label), font=font, fill=INK)


def _h_end_card(f: Frame) -> None:
    w, h = f.size
    title = _title_text(f.params) or "Thanks!"
    subtitle = f.params.get("subtitle") or f.params.get("cta")
    font = f.font(h * 0.10)
    progress = P.ease_out_cubic(min(1.0, f.t / 0.5))
    lines = P.wrap_text(f.draw, title, font, w * 0.84)
    P.write_on_lines(f.draw, (w / 2, h * 0.42), lines, font, f.palette[0], progress)
    if subtitle and f.t > 0.5:
        sf = f.font(h * 0.05)
        tw, _ = P.text_size(f.draw, str(subtitle), sf)
        f.draw.text((w / 2 - tw / 2, h * 0.62), str(subtitle), font=sf, fill=INK)


def _h_camera_pan(f: Frame) -> None:
    w, h = f.size
    items = f.params.get("items") or P.SPRITE_NAMES[:4]
    n = len(items)
    span = w * 1.8
    pan = -(span - w) * P.ease_in_out(f.t)
    sz = min(w, h) * 0.16
    for i, name in enumerate(items):
        cx = pan + span * (i + 0.5) / n
        cy = h * 0.5 + math.sin(i * 1.3) * h * 0.08
        if -sz < cx < w + sz:
            P.sprite(f.draw, str(name), (cx - sz / 2, cy - sz / 2, cx + sz / 2, cy + sz / 2), accent(i, f.palette), f.rng)


def _h_custom_sprite_path(f: Frame) -> None:
    w, h = f.size
    name = f.params.get("sprite") or "star"
    path = f.params.get("path") or [[0.1, 0.5], [0.9, 0.5]]
    pts = [(float(p[0]) * w, float(p[1]) * h) for p in path]
    if len(pts) == 1:
        pts = pts * 2
    seg = P.clamp01(f.t) * (len(pts) - 1)
    i = min(int(seg), len(pts) - 2)
    frac = seg - i
    x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * frac
    y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * frac
    # trail
    for j in range(1, i + 1):
        P.sketch_line(f.draw, pts[j - 1], pts[j], accent(1, f.palette), 3, f.rng, 1.5)
    P.sketch_line(f.draw, pts[i], (x, y), accent(1, f.palette), 3, f.rng, 1.5)
    sz = min(w, h) * 0.14
    P.sprite(f.draw, name, (x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2), accent(0, f.palette), f.rng)


SCENE_HANDLERS: dict[str, Callable[[Frame], None]] = {
    "hook_claim": _h_text,
    "hook_question": _h_text,
    "pattern_interrupt": _h_text,
    "title_writeon": _h_text,
    "boxes_popin": _h_boxes,
    "object_hop": _h_object_hop,
    "arrow_flow": _h_arrow_flow,
    "celebration": _h_celebration,
    "end_card": _h_end_card,
    "camera_pan": _h_camera_pan,
    "custom_sprite_path": _h_custom_sprite_path,
}


# --- caption (engine-drawn, safe-zone aware) --------------------------------

def _draw_caption(draw, text: str, size: tuple[int, int], aspect: str, palette: list[str]) -> None:
    w, h = size
    top_z, bot_z = SAFE_ZONES.get(aspect, (0.05, 0.06))
    font = get_font(int(h * 0.045))
    lines = P.wrap_text(draw, text, font, w * 0.86)
    _, lh = P.text_size(draw, "Ag", font)
    y = h * (1 - bot_z) - lh * len(lines) - h * 0.01
    for ln in lines:
        tw, _ = P.text_size(draw, ln, font)
        x = w / 2 - tw / 2
        # paper halo for legibility over busy frames
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            draw.text((x + dx, y + dy), ln, font=font, fill=PAPER)
        draw.text((x, y), ln, font=font, fill=INK)
        y += lh * 1.2


# --- spec helpers ------------------------------------------------------------

def _as_dict(spec: Any) -> dict:
    if hasattr(spec, "model_dump"):
        return spec.model_dump()
    return dict(spec)


def resolution_for(spec: dict) -> tuple[int, int]:
    res = spec.get("resolution")
    if res:
        return (int(res[0]), int(res[1]))
    return ASPECT_PRESETS.get(spec.get("aspect", "16:9"), ASPECT_PRESETS["16:9"])


def load_spec(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- rendering ---------------------------------------------------------------

def render_frame(spec: dict, scene_idx: int, frame_idx: int, n_frames: int) -> Image.Image:
    """Render a single frame of a single scene — used directly by golden-frame tests."""
    scene = spec["scenes"][scene_idx]
    size = resolution_for(spec)
    palette = spec.get("palette_override") or PALETTE
    img = Image.new("RGB", size, PAPER)
    draw = ImageDraw.Draw(img)
    t = 0.0 if n_frames <= 1 else frame_idx / (n_frames - 1)
    frame = Frame(
        img=img,
        draw=draw,
        t=t,
        dur=float(scene.get("duration_s", 2.0)),
        size=size,
        palette=palette,
        rng=P.rng(scene_idx * 1_000_003 + frame_idx),
        scene_seed=scene_idx * 7919 + 13,
        params=_clean_params(scene.get("params") or {}),
    )
    handler = SCENE_HANDLERS.get(scene["type"])
    if handler is None:
        raise ValueError(f"no engine handler for scene type {scene['type']!r}")
    handler(frame)
    caption = scene.get("caption")
    if caption:
        _draw_caption(draw, clean_text(str(caption)), size, spec.get("aspect", "16:9"), palette)
    return img


def render_spec(
    spec: Any,
    out_path: str | Path,
    *,
    draft: bool = False,
    workdir: str | Path | None = None,
) -> str:
    """Render a full spec to an MP4 at ``out_path``. Returns the path as a string."""
    spec = _as_dict(spec)
    fps = 12 if draft else int(spec.get("fps", 30))
    scenes = spec["scenes"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="sketchmotion_"))
    tmp.mkdir(parents=True, exist_ok=True)
    frame_no = 0
    try:
        for si, scene in enumerate(scenes):
            n = max(1, int(round(float(scene.get("duration_s", 2.0)) * fps)))
            for fi in range(n):
                img = render_frame(spec, si, fi, n)
                img.save(tmp / f"f{frame_no:05d}.png")
                frame_no += 1
        if frame_no == 0:
            raise ValueError("spec produced zero frames")
        _encode(tmp, fps, out_path)
    finally:
        if workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)
    return str(out_path)


def _encode(frames_dir: Path, fps: int, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(frames_dir / "f%05d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m render.engine <spec.json> [out.mp4]", file=sys.stderr)
        return 2
    spec_path = argv[0]
    out = argv[1] if len(argv) > 1 else "out.mp4"
    spec = load_spec(spec_path)
    path = render_spec(spec, out)
    print(f"rendered -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
