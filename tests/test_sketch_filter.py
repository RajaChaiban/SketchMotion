"""Per-frame sketch filter: both styles transform a real-ish frame without error."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from render.sketch_filter import STYLES, sketchify


def _photo(size=(120, 90)) -> Image.Image:
    """A synthetic 'photographic' frame: smooth gradients + a hard-edged shape."""
    w, h = size
    xx, yy = np.meshgrid(np.linspace(0, 255, w), np.linspace(0, 255, h))
    arr = np.stack([xx, yy, (xx + yy) / 2], axis=-1).astype(np.uint8)
    arr[20:60, 30:80] = [200, 40, 40]  # a block to create edges
    return Image.fromarray(arr, "RGB")


@pytest.mark.parametrize("style", STYLES)
def test_sketchify_preserves_size_and_changes_pixels(style):
    src = _photo()
    out = sketchify(src, style=style)
    assert out.size == src.size
    assert out.mode == "RGB"
    # output must differ from the input (it actually transformed something)
    assert not np.array_equal(np.asarray(src), np.asarray(out))
    # not a blank frame
    assert (np.asarray(out) < 250).any()


def test_pencil_is_grayscale_ish():
    out = sketchify(_photo(), style="pencil")
    a = np.asarray(out).astype(int)
    # R, G, B nearly equal everywhere (paper is near-neutral) -> low channel spread
    spread = np.abs(a[..., 0] - a[..., 2]).mean()
    assert spread < 12


def test_ink_keeps_some_color():
    out = sketchify(_photo(), style="ink")
    a = np.asarray(out).astype(int)
    spread = np.abs(a[..., 0] - a[..., 2]).mean()
    assert spread > 5  # color survives in the ink style


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        sketchify(_photo(), style="watercolor")
