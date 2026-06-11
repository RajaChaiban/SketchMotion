"""Hand-drawn sketch primitives.

Every drawable takes its stroke/jitter parameters as arguments (not globals) so a
future StyleProfile is configuration, not new code. Jitter is numpy-vectorised and
seeded by the caller for determinism.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from PIL import ImageDraw

Point = tuple[float, float]


# --- easing ------------------------------------------------------------------

def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp01(t)
    return 3 * t * t - 2 * t * t * t


def ease_out_back(t: float) -> float:
    t = clamp01(t)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def rng(seed: int) -> np.random.RandomState:
    return np.random.RandomState(int(seed) & 0xFFFFFFFF)


# --- stroke helpers ----------------------------------------------------------

def jitter_points(points: Sequence[Point], amp: float, r: np.random.RandomState) -> list[Point]:
    pts = np.asarray(points, dtype=float)
    if amp <= 0 or pts.size == 0:
        return [(float(x), float(y)) for x, y in pts]
    noise = r.uniform(-amp, amp, size=pts.shape)
    out = pts + noise
    return [(float(x), float(y)) for x, y in out]


def sketch_line(
    draw: ImageDraw.ImageDraw,
    p1: Point,
    p2: Point,
    color: str,
    width: int = 3,
    r: np.random.RandomState | None = None,
    jitter_amp: float = 2.0,
    segments: int | None = None,
) -> None:
    r = r if r is not None else rng(0)
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    n = segments or max(2, int(dist // 18) + 2)
    ts = np.linspace(0, 1, n)
    base = [(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t) for t in ts]
    pts = jitter_points(base, jitter_amp, r)
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=width, joint="curve")


def sketch_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    color: str,
    width: int = 3,
    r: np.random.RandomState | None = None,
    jitter_amp: float = 2.0,
) -> None:
    r = r if r is not None else rng(0)
    x0, y0, x1, y1 = box
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(corners[:-1], corners[1:]):
        sketch_line(draw, a, b, color, width, r, jitter_amp)


def sketch_ellipse(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    color: str,
    width: int = 3,
    r: np.random.RandomState | None = None,
    jitter_amp: float = 2.0,
    n: int = 44,
) -> None:
    r = r if r is not None else rng(0)
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    ang = np.linspace(0, 2 * math.pi, n)
    base = [(cx + rx * math.cos(a), cy + ry * math.sin(a)) for a in ang]
    pts = jitter_points(base, jitter_amp, r)
    draw.line(pts, fill=color, width=width, joint="curve")


def sketch_arrow(
    draw: ImageDraw.ImageDraw,
    p1: Point,
    p2: Point,
    color: str,
    width: int = 3,
    r: np.random.RandomState | None = None,
    jitter_amp: float = 2.0,
    head: float = 18.0,
) -> None:
    r = r if r is not None else rng(0)
    sketch_line(draw, p1, p2, color, width, r, jitter_amp)
    x1, y1 = p1
    x2, y2 = p2
    ang = math.atan2(y2 - y1, x2 - x1)
    for da in (math.radians(152), math.radians(-152)):
        hx = x2 + head * math.cos(ang + da)
        hy = y2 + head * math.sin(ang + da)
        sketch_line(draw, (x2, y2), (hx, hy), color, width, r, jitter_amp * 0.5)


# --- text --------------------------------------------------------------------

def text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if text_size(draw, trial, font)[0] <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def write_on_lines(
    draw: ImageDraw.ImageDraw,
    center: Point,
    lines: list[str],
    font,
    color: str,
    progress: float = 1.0,
    line_gap: float = 1.25,
) -> None:
    """Reveal multi-line text left-to-right, top-to-bottom, centered on `center`."""
    progress = clamp01(progress)
    total_chars = sum(len(ln) for ln in lines) or 1
    reveal = int(round(progress * total_chars))
    _, line_h = text_size(draw, "Ag", font)
    step = line_h * line_gap
    block_h = step * len(lines)
    cx, cy = center
    y = cy - block_h / 2
    consumed = 0
    for ln in lines:
        take = max(0, min(len(ln), reveal - consumed))
        shown = ln[:take]
        consumed += len(ln)
        if shown:
            w, _ = text_size(draw, ln, font)  # center on the full line width
            draw.text((cx - w / 2, y), shown, font=font, fill=color)
        y += step


# --- sprites -----------------------------------------------------------------

def _poly(draw, pts, color, width, r, amp):
    pts = jitter_points(pts, amp, r)
    draw.line(pts + [pts[0]], fill=color, width=width, joint="curve")


def trophy(draw, box, color, r=None, width=4):
    r = r if r is not None else rng(0)
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    cup = (x0 + w * 0.22, y0, x0 + w * 0.78, y0 + h * 0.55)
    sketch_ellipse(draw, (cup[0], cup[1] - h * 0.04, cup[2], cup[1] + h * 0.14), color, width, r, 1.5)
    sketch_line(draw, (cup[0], cup[1] + h * 0.05), (x0 + w * 0.4, cup[3]), color, width, r, 1.5)
    sketch_line(draw, (cup[2], cup[1] + h * 0.05), (x0 + w * 0.6, cup[3]), color, width, r, 1.5)
    # handles
    sketch_ellipse(draw, (x0 + w * 0.05, cup[1], x0 + w * 0.28, cup[1] + h * 0.3), color, width, r, 1.5)
    sketch_ellipse(draw, (x0 + w * 0.72, cup[1], x0 + w * 0.95, cup[1] + h * 0.3), color, width, r, 1.5)
    # stem + base
    sketch_line(draw, (x0 + w * 0.5, cup[3]), (x0 + w * 0.5, y0 + h * 0.78), color, width, r, 1.5)
    sketch_line(draw, (x0 + w * 0.32, y1), (x0 + w * 0.68, y1), color, width, r, 1.5)
    sketch_line(draw, (x0 + w * 0.42, y0 + h * 0.78), (x0 + w * 0.58, y0 + h * 0.78), color, width, r, 1.5)


def _sprite_star(draw, box, color, r, width=4):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    R = min(x1 - x0, y1 - y0) / 2
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else R * 0.42
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    _poly(draw, pts, color, width, r, 1.5)


def _sprite_ball(draw, box, color, r, width=4):
    sketch_ellipse(draw, box, color, width, r, 1.5)
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rr = (x1 - x0) / 2
    for ang in range(0, 360, 72):
        a = math.radians(ang)
        sketch_line(draw, (cx, cy), (cx + rr * 0.55 * math.cos(a), cy + rr * 0.55 * math.sin(a)), color, max(2, width - 1), r, 1.0)


def _sprite_rocket(draw, box, color, r, width=4):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    body = [(cx, y0), (x1 - (x1 - x0) * 0.25, y0 + (y1 - y0) * 0.6),
            (x1 - (x1 - x0) * 0.25, y1 - (y1 - y0) * 0.2),
            (x0 + (x1 - x0) * 0.25, y1 - (y1 - y0) * 0.2),
            (x0 + (x1 - x0) * 0.25, y0 + (y1 - y0) * 0.6)]
    _poly(draw, body, color, width, r, 1.5)
    sketch_ellipse(draw, (cx - (x1 - x0) * 0.1, y0 + (y1 - y0) * 0.32,
                          cx + (x1 - x0) * 0.1, y0 + (y1 - y0) * 0.52), color, width, r, 1.0)


def _sprite_bulb(draw, box, color, r, width=4):
    x0, y0, x1, y1 = box
    sketch_ellipse(draw, (x0, y0, x1, y0 + (y1 - y0) * 0.72), color, width, r, 1.5)
    sketch_line(draw, (x0 + (x1 - x0) * 0.35, y0 + (y1 - y0) * 0.75),
                (x0 + (x1 - x0) * 0.65, y0 + (y1 - y0) * 0.75), color, width, r, 1.0)
    sketch_line(draw, (x0 + (x1 - x0) * 0.38, y1 - (y1 - y0) * 0.12),
                (x0 + (x1 - x0) * 0.62, y1 - (y1 - y0) * 0.12), color, width, r, 1.0)


def _sprite_heart(draw, box, color, r, width=4):
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = (x1 - x0) / 2
    pts = []
    for i in range(40):
        t = i / 39 * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x / 16 * w, cy - y / 16 * w))
    _poly(draw, pts, color, width, r, 1.0)


def basketball(draw, center, radius, color="#e8763a", r=None, width=4):
    """A hand-drawn basketball: circle + seams."""
    r = r if r is not None else rng(0)
    cx, cy = center
    sketch_ellipse(draw, (cx - radius, cy - radius, cx + radius, cy + radius), color, width, r, 1.0)
    seam = max(2, width - 1)
    sketch_line(draw, (cx, cy - radius), (cx, cy + radius), color, seam, r, 0.8)
    sketch_line(draw, (cx - radius, cy), (cx + radius, cy), color, seam, r, 0.8)
    # two curved seams (left/right) approximated with jittered arcs
    n = 14
    for sign in (-1, 1):
        pts = []
        for i in range(n):
            a = -math.pi / 2 + math.pi * i / (n - 1)
            pts.append((cx + sign * radius * 0.55 * math.cos(a), cy + radius * math.sin(a)))
        pts = jitter_points(pts, 0.6, r)
        draw.line(pts, fill=color, width=seam, joint="curve")


def hoop(draw, rim_center, rim_w, color="#1e1e1e", rim_color="#e8763a", r=None, width=4):
    """Backboard (right) + rim ellipse (facing left) + a hanging net, around `rim_center`."""
    r = r if r is not None else rng(0)
    cx, cy = rim_center
    rim_h = rim_w * 0.34
    # backboard: vertical board just behind (right of) the rim
    bb_x = cx + rim_w * 0.45
    bb_w = rim_w * 0.18
    bb_h = rim_w * 1.1
    sketch_rect(draw, (bb_x, cy - bb_h * 0.75, bb_x + bb_w, cy + bb_h * 0.25), color, width, r, 1.2)
    sketch_rect(draw, (bb_x, cy - bb_h * 0.4, bb_x + bb_w, cy + bb_h * 0.05),
                color, max(2, width - 1), r, 0.8)
    # rim
    rim_box = (cx - rim_w / 2, cy - rim_h / 2, cx + rim_w / 2, cy + rim_h / 2)
    sketch_ellipse(draw, rim_box, rim_color, width, r, 0.8)
    # net: lines from around the rim converging to a narrower bottom
    netd = rim_w * 0.85
    bottom = cy + netd
    top_pts = [(cx + rim_w * 0.5 * math.cos(a), cy + rim_h * 0.5 * math.sin(a))
               for a in [math.radians(d) for d in (200, 245, 290, 335, 20)]]
    bot_w = rim_w * 0.28
    bot_pts = [(cx - bot_w / 2 + bot_w * k, bottom) for k in (0, 0.33, 0.66, 1.0)]
    for tp in top_pts:
        bx = cx + (tp[0] - cx) * 0.35
        sketch_line(draw, tp, (bx, bottom), color, max(2, width - 2), r, 0.8)
    for frac in (0.45, 0.8):
        yy = cy + rim_h * 0.3 + netd * frac
        sketch_line(draw, (cx - rim_w * 0.42 * (1 - frac), yy),
                    (cx + rim_w * 0.42 * (1 - frac), yy), color, max(2, width - 2), r, 0.8)
    return {"rim_center": (cx, cy), "rim_w": rim_w, "net_bottom": bottom}


def stick_figure(draw, box, color, r=None, width=4, arms="side"):
    """A hand-drawn stick figure. `arms`: 'side' | 'up' (both raised) | 'reach' (right arm up)."""
    r = r if r is not None else rng(0)
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    head_r = (y1 - y0) * 0.12
    sketch_ellipse(draw, (cx - head_r, y0, cx + head_r, y0 + 2 * head_r), color, width, r, 1.0)
    neck = y0 + 2 * head_r
    hip = y0 + (y1 - y0) * 0.62
    shoulder = neck + (hip - neck) * 0.15
    sketch_line(draw, (cx, neck), (cx, hip), color, width, r, 1.0)
    if arms == "up":
        sketch_line(draw, (cx, shoulder), (x0 + (x1 - x0) * 0.2, y0 - head_r * 1.2), color, width, r, 1.0)
        sketch_line(draw, (cx, shoulder), (x1 - (x1 - x0) * 0.2, y0 - head_r * 1.2), color, width, r, 1.0)
    elif arms == "reach":
        sketch_line(draw, (cx, shoulder), (x0, neck + (hip - neck) * 0.5), color, width, r, 1.0)
        sketch_line(draw, (cx, shoulder), (x1 + (x1 - x0) * 0.15, y0 - head_r * 1.6), color, width, r, 1.0)
    else:  # side
        sketch_line(draw, (cx, shoulder), (x0, neck + (hip - neck) * 0.5), color, width, r, 1.0)
        sketch_line(draw, (cx, shoulder), (x1, neck + (hip - neck) * 0.5), color, width, r, 1.0)
    sketch_line(draw, (cx, hip), (x0, y1), color, width, r, 1.0)
    sketch_line(draw, (cx, hip), (x1, y1), color, width, r, 1.0)


_SPRITES = {
    "star": _sprite_star,
    "ball": _sprite_ball,
    "soccer_ball": _sprite_ball,
    "rocket": _sprite_rocket,
    "bulb": _sprite_bulb,
    "lightbulb": _sprite_bulb,
    "heart": _sprite_heart,
    "trophy": lambda d, b, c, r, width=4: trophy(d, b, c, r, width),
    "person": stick_figure,
    "stick_figure": stick_figure,
}


def sprite(draw, name: str | None, box, color: str, r=None, width: int = 4) -> str:
    r = r if r is not None else rng(0)
    key = (name or "star").lower()
    fn = _SPRITES.get(key, _sprite_star)
    fn(draw, box, color, r, width)
    return key if key in _SPRITES else "star"


SPRITE_NAMES = sorted(_SPRITES.keys())


# --- confetti ----------------------------------------------------------------

def make_confetti(n: int, size: tuple[int, int], r: np.random.RandomState) -> dict:
    w, h = size
    return {
        "x": r.uniform(0, w, n),
        "y": r.uniform(-h, 0, n),
        "vx": r.uniform(-40, 40, n),
        "vy": r.uniform(30, 120, n),
        "ci": r.randint(0, 6, n),
        "rot": r.uniform(0, math.pi, n),
    }


def confetti_at(particles: dict, elapsed_s: float, gravity: float = 320.0) -> dict:
    """Positions of particles at `elapsed_s` (closed-form, so it's stateless/deterministic)."""
    t = elapsed_s
    return {
        "x": particles["x"] + particles["vx"] * t,
        "y": particles["y"] + particles["vy"] * t + 0.5 * gravity * t * t,
        "ci": particles["ci"],
        "rot": particles["rot"],
    }


def draw_confetti(draw, snap: dict, palette: list[str], side: float = 12.0) -> None:
    xs, ys, ci = snap["x"], snap["y"], snap["ci"]
    for i in range(len(xs)):
        x = float(xs[i])
        y = float(ys[i])
        c = palette[int(ci[i]) % len(palette)]
        draw.rectangle([x, y, x + side, y + side * 0.55], fill=c)
