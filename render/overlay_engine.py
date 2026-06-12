"""Render sketch annotations onto transparent RGBA frames, for compositing over a video.

One drawer per `Annotation.type` (contract-tested). Annotations are hand-drawn (jittered
strokes, app palette) and animate over their lifetime via `easing`: `draw_on` (stroke reveals),
`pop` (scale-in), `fade` (opacity in/out). Each annotation renders to its own layer so per-
annotation opacity composites correctly.
"""
from __future__ import annotations

import math
from typing import Callable

from PIL import Image, ImageDraw

from render import primitives as P
from render.engine import draw_label, fit_label
from render.palette import INK, PALETTE, accent

RGBA = "RGBA"


def _anim(a, t: float) -> dict:
    raw = P.clamp01((t - a.t_start) / max(1e-6, a.t_end - a.t_start))
    reveal, alpha, scale = 1.0, 1.0, 1.0
    if a.easing == "draw_on":
        reveal = P.ease_out_cubic(min(1.0, raw / 0.35))
        alpha = P.clamp01(raw / 0.05) * (1.0 - P.clamp01((raw - 0.9) / 0.1))
    elif a.easing == "pop":
        scale = P.ease_out_back(min(1.0, raw / 0.3))
        alpha = P.clamp01(raw / 0.05)
    elif a.easing == "fade":
        alpha = min(P.clamp01(raw / 0.2), P.clamp01((1.0 - raw) / 0.2))
    return {"reveal": reveal, "alpha": alpha, "scale": max(0.01, scale)}


def _box_px(anchor, size, scale: float):
    w, h = size
    x, y, bw, bh = anchor
    cx, cy = (x + bw / 2) * w, (y + bh / 2) * h
    pw, ph = bw * w * scale, bh * h * scale
    return cx - pw / 2, cy - ph / 2, pw, ph


def _seed(a) -> int:
    return int(a.t_start * 1000) + int(a.anchor[0] * 997) + (hash(a.type) & 0xFFFF)


def _partial(draw, pts, reveal, color, width, r, jitter):
    n = max(2, int(len(pts) * P.clamp01(reveal)))
    seg = P.jitter_points(pts[:n], jitter, r)
    if len(seg) >= 2:
        draw.line(seg, fill=color, width=width, joint="curve")


# --- drawers -----------------------------------------------------------------

def _ellipse_pts(box, n=48):
    x0, y0, w, h = box
    cx, cy, rx, ry = x0 + w / 2, y0 + h / 2, w / 2, h / 2
    return [(cx + rx * math.cos(2 * math.pi * i / (n - 1) - math.pi / 2),
             cy + ry * math.sin(2 * math.pi * i / (n - 1) - math.pi / 2)) for i in range(n)]


def _d_circle(draw, box, reveal, params, color, r):
    _partial(draw, _ellipse_pts(box), reveal, color, 6, r, 2.0)


def _d_box(draw, box, reveal, params, color, r):
    x0, y0, w, h = box
    per = [(x0, y0)]
    for (a, b) in [((x0 + w, y0), 12), ((x0 + w, y0 + h), 12), ((x0, y0 + h), 12), ((x0, y0), 12)]:
        sx, sy = per[-1]
        per += [(sx + (a[0] - sx) * k / b, sy + (a[1] - sy) * k / b) for k in range(1, b + 1)]
    _partial(draw, per, reveal, color, 6, r, 2.0)


def _d_underline(draw, box, reveal, params, color, r):
    x0, y0, w, h = box
    y = y0 + h
    P.sketch_line(draw, (x0, y), (x0 + w * P.clamp01(reveal), y), color, 6, r, 2.0)


def _d_arrow(draw, box, reveal, params, color, r):
    x0, y0, w, h = box
    tip = (x0 + w / 2, y0 + h / 2)
    frm = (params.get("from") or "bottom").lower()
    length = max(w, h) * 1.6 + 40
    tail = {"bottom": (tip[0], tip[1] + length), "top": (tip[0], tip[1] - length),
            "left": (tip[0] - length, tip[1]), "right": (tip[0] + length, tip[1])}.get(frm, (tip[0], tip[1] + length))
    cur = (tail[0] + (tip[0] - tail[0]) * reveal, tail[1] + (tip[1] - tail[1]) * reveal)
    P.sketch_line(draw, tail, cur, color, 6, r, 2.0)
    if reveal > 0.85:
        P.sketch_arrow(draw, tail, tip, color, 6, r, 2.0, head=26)


def _d_callout(draw, box, reveal, params, color, r, size):
    x0, y0, w, h = box
    text = str(params.get("text", ""))
    px = max(20, int(size[1] * 0.045))
    font, lines, lh = fit_label(draw, text, size[0] * 0.4, size[1] * 0.2, px)
    tx, ty = x0 + w + 16, max(8, y0 - lh)
    if tx + size[0] * 0.3 > size[0]:
        tx = x0 - size[0] * 0.32
    P.sketch_line(draw, (x0 + w / 2, y0), (tx, ty + lh / 2), color, 4, r, 1.5)
    n = int(round(P.clamp01(reveal) * sum(len(s) for s in lines)))
    consumed = 0
    for ln in lines:
        take = max(0, min(len(ln), n - consumed)); consumed += len(ln)
        if take:
            draw.text((tx, ty), ln[:take], font=font, fill=INK)
        ty += lh


def _d_caption(draw, box, reveal, params, color, r, size):
    text = str(params.get("text", ""))
    w, h = size
    px = int(h * 0.06)
    font, lines, lh = fit_label(draw, text, w * 0.86, h * 0.2, px)
    y = h * 0.82 - lh * len(lines)
    n = int(round(P.clamp01(reveal) * sum(len(s) for s in lines)))
    consumed = 0
    for ln in lines:
        take = max(0, min(len(ln), n - consumed)); consumed += len(ln)
        tw = P.text_size(draw, ln, font)[0]
        x = w / 2 - tw / 2
        if take:
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                draw.text((x + dx, y + dy), ln[:take], font=font, fill=(251, 249, 244, 255))
            draw.text((x, y), ln[:take], font=font, fill=color)
        consumed_y = lh
        y += consumed_y


def _d_sprite(draw, box, reveal, params, color, r):
    x0, y0, w, h = box
    P.sprite(draw, params.get("sprite", "star"), (x0, y0, x0 + w, y0 + h), color, r)


def _d_progress(draw, box, reveal, params, color, r, size, t):
    w, h = size
    dur = float(params.get("duration", 1.0)) or 1.0
    frac = P.clamp01(t / dur)
    y = h - max(8, int(h * 0.03))
    P.sketch_line(draw, (w * 0.06, y), (w * 0.94, y), INK, 4, r, 1.0)
    P.sketch_line(draw, (w * 0.06, y), (w * 0.06 + (w * 0.88) * frac, y), color, 7, r, 1.5)


def _draw_annotation(layer: Image.Image, a, t: float, palette: list[str]) -> None:
    d = ImageDraw.Draw(layer)
    anim = _anim(a, t)
    box = _box_px(a.anchor, layer.size, anim["scale"])
    r = P.rng(_seed(a))
    color = a.params.get("color") or accent(0, palette)
    typ = a.type
    if typ == "circle_highlight":
        _d_circle(d, box, anim["reveal"], a.params, color, r)
    elif typ == "box_outline":
        _d_box(d, box, anim["reveal"], a.params, color, r)
    elif typ == "underline":
        _d_underline(d, box, anim["reveal"], a.params, color, r)
    elif typ == "arrow_point":
        _d_arrow(d, box, anim["reveal"], a.params, color, r)
    elif typ == "callout_text":
        _d_callout(d, box, anim["reveal"], a.params, color, r, layer.size)
    elif typ == "writeon_caption":
        _d_caption(d, box, anim["reveal"], a.params, color, r, layer.size)
    elif typ == "sprite_react":
        _d_sprite(d, box, anim["reveal"], a.params, color, r)
    elif typ == "progress_bar":
        _d_progress(d, box, anim["reveal"], a.params, color, r, layer.size, t)
    else:  # pragma: no cover - guarded by the contract test
        raise ValueError(f"no drawer for annotation type {typ!r}")

    if anim["alpha"] < 0.999:
        rr, gg, bb, al = layer.split()
        al = al.point(lambda v: int(v * anim["alpha"]))
        layer.paste(Image.merge(RGBA, (rr, gg, bb, al)))


ANNOTATION_TYPES = {
    "circle_highlight", "box_outline", "underline", "arrow_point",
    "callout_text", "writeon_caption", "sprite_react", "progress_bar",
}


def active_at(annotations, t: float) -> list:
    return [a for a in annotations if a.t_start <= t <= a.t_end]


def render_overlay_frame(annotations, t: float, size: tuple[int, int],
                         palette: list[str] | None = None) -> Image.Image:
    """A transparent RGBA frame with every annotation active at time `t` drawn on it."""
    palette = palette or PALETTE
    frame = Image.new(RGBA, size, (0, 0, 0, 0))
    for a in active_at(annotations, t):
        layer = Image.new(RGBA, size, (0, 0, 0, 0))
        _draw_annotation(layer, a, t, palette)
        frame = Image.alpha_composite(frame, layer)
    return frame
