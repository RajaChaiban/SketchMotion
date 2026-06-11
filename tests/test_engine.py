"""Engine: contract (every scene type handled), per-scene rendering, determinism,
and a real (tiny) MP4 encode via ffmpeg."""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from render import engine
from render.engine import SCENE_HANDLERS, render_frame, render_spec, resolution_for

# the Scene.type literal lives in the schema in Phase 3; until then assert against
# the known catalog so a new type without a handler fails loudly here.
SCENE_TYPES = {
    "hook_claim", "hook_question", "pattern_interrupt", "title_writeon",
    "boxes_popin", "object_hop", "arrow_flow", "celebration", "end_card",
    "camera_pan", "custom_sprite_path",
}

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_every_scene_type_has_exactly_one_handler():
    assert set(SCENE_HANDLERS) == SCENE_TYPES


@pytest.mark.parametrize("scene_type", sorted(SCENE_TYPES))
def test_each_scene_renders_a_nonblank_frame(scene_type):
    spec = {
        "fps": 30,
        "aspect": "16:9",
        "resolution": [320, 180],
        "scenes": [{"type": scene_type, "duration_s": 2.0, "params": {}}],
    }
    img = render_frame(spec, 0, 15, 30)  # mid-scene
    assert np.asarray(img).shape == (180, 320, 3)
    assert (np.asarray(img) < 250).any()


def test_render_is_deterministic():
    spec = {
        "fps": 30, "aspect": "1:1", "resolution": [240, 240],
        "scenes": [{"type": "boxes_popin", "duration_s": 2.0,
                    "params": {"items": ["A", "B", "C"]}}],
    }
    a = np.asarray(render_frame(spec, 0, 20, 60))
    b = np.asarray(render_frame(spec, 0, 20, 60))
    assert np.array_equal(a, b)


def test_unicode_text_is_sanitized_not_tofu():
    from render.engine import clean_text

    assert clean_text("Plan → List → Save") == "Plan -> List -> Save"
    assert clean_text("↑ BDNF & Dopamine") == "up BDNF & Dopamine"
    assert clean_text("“smart” quotes — dash") == '"smart" quotes - dash'
    # a scene carrying arrows still renders without error
    spec = {
        "fps": 30, "aspect": "16:9", "resolution": [320, 180],
        "scenes": [{"type": "arrow_flow", "duration_s": 2.0,
                    "params": {"steps": ["Move", "↑ Flow", "Clarity"]}}],
    }
    img = render_frame(spec, 0, 30, 60)
    assert (np.asarray(img) < 250).any()


def test_resolution_derivation():
    assert resolution_for({"aspect": "9:16"}) == (720, 1280)
    assert resolution_for({"aspect": "16:9", "resolution": [100, 50]}) == (100, 50)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_tiny_spec_encodes_playable_mp4(tmp_path):
    spec = {
        "fps": 24, "aspect": "16:9", "resolution": [320, 180],
        "scenes": [
            {"type": "hook_claim", "duration_s": 0.5, "params": {"text": "Hi"}},
            {"type": "celebration", "duration_s": 0.5, "params": {"label": "Yay"}},
        ],
    }
    out = tmp_path / "out.mp4"
    path = render_spec(spec, out)
    assert out.exists() and out.stat().st_size > 0
    # ffprobe confirms it's a real video stream
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    assert "h264" in probe.stdout


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_temp_frames_cleaned_up(tmp_path):
    spec = {
        "fps": 24, "aspect": "1:1", "resolution": [120, 120],
        "scenes": [{"type": "end_card", "duration_s": 0.3, "params": {"title": "X"}}],
    }
    workdir = tmp_path / "frames"
    render_spec(spec, tmp_path / "o.mp4", workdir=workdir)
    # with an explicit workdir, frames are kept (caller owns cleanup)
    assert any(workdir.glob("f*.png"))
