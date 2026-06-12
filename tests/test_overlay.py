"""Overlay annotations: schema guardrails, the drawer contract, time-gating, compositing."""
from __future__ import annotations

import shutil
import subprocess
from typing import get_args

import numpy as np
import pytest

from render.overlay_engine import ANNOTATION_TYPES, active_at, render_overlay_frame
from worker.overlay_spec import AnnotationType, OverlaySpec, validate_overlay_spec

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _ann(type_, t0=0.0, t1=2.0, anchor=(0.4, 0.4, 0.2, 0.2), **params):
    return {"type": type_, "t_start": t0, "t_end": t1, "anchor": list(anchor), "params": params}


def _spec(annotations, w=320, h=180, fps=30):
    return {"source_fps": fps, "source_resolution": [w, h], "annotations": annotations}


# --- schema ------------------------------------------------------------------

def test_valid_spec():
    s = validate_overlay_spec(_spec([_ann("circle_highlight"),
                                     _ann("callout_text", text="Look!", anchor=(0.1, 0.1, 0.15, 0.1))]))
    assert isinstance(s, OverlaySpec) and len(s.annotations) == 2


def test_anchor_out_of_frame_rejected():
    with pytest.raises(Exception):
        validate_overlay_spec(_spec([_ann("box_outline", anchor=(0.9, 0.4, 0.3, 0.2))]))


def test_bad_time_order_rejected():
    with pytest.raises(Exception):
        validate_overlay_spec(_spec([_ann("circle_highlight", t0=3.0, t1=1.0)]))


def test_callout_requires_text():
    with pytest.raises(Exception):
        validate_overlay_spec(_spec([_ann("callout_text", anchor=(0.1, 0.1, 0.1, 0.1))]))


def test_coverage_cap_30_percent():
    big = [_ann("box_outline", anchor=(0.0, 0.0, 0.5, 0.5)),
           _ann("box_outline", anchor=(0.5, 0.5, 0.4, 0.4))]  # ~25% + ~16% at t=0 -> >30%
    with pytest.raises(Exception):
        validate_overlay_spec(_spec(big))


# --- drawer contract ---------------------------------------------------------

def test_every_annotation_type_has_a_drawer():
    assert ANNOTATION_TYPES == set(get_args(AnnotationType))


@pytest.mark.parametrize("typ", sorted(get_args(AnnotationType)))
def test_each_type_draws_ink(typ):
    a = validate_overlay_spec(_spec([_ann(typ, text="Hi", duration=2.0, sprite="star")])).annotations[0]
    img = render_overlay_frame([a], t=1.0, size=(320, 180))
    alpha = np.asarray(img)[..., 3]
    assert (alpha > 0).any()  # something was drawn


# --- time gating -------------------------------------------------------------

def test_inactive_annotation_renders_transparent():
    a = validate_overlay_spec(_spec([_ann("circle_highlight", t0=0.0, t1=1.0)])).annotations[0]
    img = render_overlay_frame([a], t=5.0, size=(320, 180))  # past its window
    assert not (np.asarray(img)[..., 3] > 0).any()


def test_active_at_filters_by_time():
    anns = validate_overlay_spec(_spec([
        _ann("circle_highlight", t0=0.0, t1=1.0),
        _ann("box_outline", t0=2.0, t1=3.0),
    ])).annotations
    assert len(active_at(anns, 0.5)) == 1
    assert len(active_at(anns, 2.5)) == 1
    assert len(active_at(anns, 1.5)) == 0


# --- compositing -------------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_composite_produces_h264_with_audio(tmp_path):
    from worker.overlay import composite_annotations

    src = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-pix_fmt", "yuv420p", "-shortest", str(src)], check=True)
    spec = validate_overlay_spec(_spec([
        _ann("circle_highlight", t0=0.0, t1=1.0, anchor=(0.4, 0.4, 0.2, 0.2)),
        _ann("callout_text", t0=0.2, t1=1.0, anchor=(0.1, 0.1, 0.1, 0.1), text="Hi"),
    ], w=160, h=120, fps=10))
    out = tmp_path / "out.mp4"
    composite_annotations(src, spec, out)
    assert out.exists()
    v = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout
    assert "h264" in v and "audio" in a
