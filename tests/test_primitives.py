"""Each primitive must render without error and put ink on the page."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from render import primitives as P
from render.palette import PALETTE


def _canvas(size=(200, 200)):
    img = Image.new("RGB", size, "#ffffff")
    return img, ImageDraw.Draw(img)


def _non_blank(img) -> bool:
    arr = np.asarray(img)
    return bool((arr < 250).any())


def test_easings_bounded():
    for fn in (P.ease_out_cubic, P.ease_in_out, P.ease_out_back):
        assert abs(fn(0.0)) < 1e-6
        assert abs(fn(1.0) - 1.0) < 1e-6
    assert P.clamp01(-5) == 0.0
    assert P.clamp01(5) == 1.0


def test_jitter_is_seeded_and_reproducible():
    base = [(0, 0), (10, 10), (20, 5)]
    a = P.jitter_points(base, 3.0, P.rng(42))
    b = P.jitter_points(base, 3.0, P.rng(42))
    assert a == b
    c = P.jitter_points(base, 3.0, P.rng(7))
    assert a != c


@pytest.mark.parametrize(
    "call",
    [
        lambda d, r: P.sketch_line(d, (10, 10), (180, 180), PALETTE[0], 4, r),
        lambda d, r: P.sketch_rect(d, (20, 20, 180, 120), PALETTE[1], 4, r),
        lambda d, r: P.sketch_ellipse(d, (20, 20, 180, 120), PALETTE[2], 4, r),
        lambda d, r: P.sketch_arrow(d, (20, 100), (180, 100), PALETTE[3], 4, r),
        lambda d, r: P.trophy(d, (40, 20, 160, 180), PALETTE[4], r),
        lambda d, r: P.stick_figure(d, (70, 20, 130, 180), PALETTE[5], r),
    ],
)
def test_stroke_primitives_draw_ink(call):
    img, draw = _canvas()
    call(draw, P.rng(1))
    assert _non_blank(img)


@pytest.mark.parametrize("name", P.SPRITE_NAMES)
def test_every_sprite_draws(name):
    img, draw = _canvas()
    P.sprite(draw, name, (40, 40, 160, 160), PALETTE[0], P.rng(3))
    assert _non_blank(img)


def test_write_on_partial_reveal():
    img, draw = _canvas((400, 120))
    P.write_on_lines(draw, (200, 60), ["Hello world"], _font(), "#000000", 0.0)
    assert not _non_blank(img)  # nothing revealed at progress 0
    P.write_on_lines(draw, (200, 60), ["Hello world"], _font(), "#000000", 1.0)
    assert _non_blank(img)


def test_confetti_falls_over_time():
    parts = P.make_confetti(50, (200, 200), P.rng(0))
    early = P.confetti_at(parts, 0.1)
    late = P.confetti_at(parts, 1.0)
    assert float(np.mean(late["y"])) > float(np.mean(early["y"]))
    img, draw = _canvas()
    P.draw_confetti(draw, P.confetti_at(parts, 0.5), PALETTE)
    assert _non_blank(img)


def _font():
    from render.fonts import get_font

    return get_font(40)
