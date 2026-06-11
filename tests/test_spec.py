"""SceneSpec validation: valid golden fixtures pass and render; invalid specs raise
precise field-level errors."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from render.engine import render_frame
from worker.spec import SceneSpec, validate_spec

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted(FIXTURES.glob("valid_*.json"))


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_specs_pass_and_derive_resolution(path):
    spec = validate_spec(json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(spec, SceneSpec)
    assert spec.resolution is not None  # derived from aspect
    assert abs(sum(s.duration_s for s in spec.scenes) - spec.total_duration_s) <= 0.5


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_specs_are_renderable(path):
    """A validated spec must round-trip into the engine (model_dump -> render)."""
    spec = validate_spec(json.loads(path.read_text(encoding="utf-8")))
    data = spec.model_dump()
    img = render_frame(data, 0, 0, 30)
    assert img.size == tuple(spec.resolution)


# --- invalid specs: each must fail, on the right field -----------------------

def _base() -> dict:
    return {
        "title": "T",
        "aspect": "16:9",
        "scenes": [
            {"type": "hook_claim", "duration_s": 2.0, "params": {"text": "Hi"}},
            {"type": "end_card", "duration_s": 2.0, "params": {"title": "Bye"}},
        ],
        "total_duration_s": 4.0,
    }


def test_invalid_first_scene_not_a_hook():
    data = _base()
    data["scenes"][0] = {"type": "boxes_popin", "duration_s": 2.0, "params": {"items": ["a", "b"]}}
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "hook" in str(exc.value)


def test_invalid_hook_too_long():
    data = _base()
    data["scenes"][0]["duration_s"] = 4.0
    data["total_duration_s"] = 6.0
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "hook scene" in str(exc.value)


def test_invalid_too_few_scenes():
    data = _base()
    data["scenes"] = data["scenes"][:1]
    data["total_duration_s"] = 2.0
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "scenes" in str(exc.value)


def test_invalid_box_params_one_item():
    data = _base()
    data["scenes"][1] = {"type": "boxes_popin", "duration_s": 2.0, "params": {"items": ["only one"]}}
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "items" in str(exc.value)


def test_invalid_scene_duration_over_cap():
    data = _base()
    data["scenes"][1]["duration_s"] = 9.0
    data["total_duration_s"] = 11.0
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "duration_s" in str(exc.value)


# --- contextual validators ---------------------------------------------------

def test_duration_fidelity_rejects_off_target():
    data = _base()  # total 4.0s
    with pytest.raises(ValidationError) as exc:
        validate_spec(data, target_duration_s=30)
    assert "within 10%" in str(exc.value)


def test_duration_fidelity_accepts_on_target():
    data = _base()  # total 4.0s
    spec = validate_spec(data, target_duration_s=4)  # exactly on target
    assert spec.total_duration_s == 4.0


def test_total_duration_must_match_scene_sum():
    data = _base()
    data["total_duration_s"] = 20.0  # scenes only sum to 4
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "sum of" in str(exc.value)


def test_bad_palette_hex_rejected():
    data = _base()
    data["palette_override"] = ["#ff6b4a", "not-a-color"]
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "hex" in str(exc.value)


def test_custom_path_must_be_normalized():
    data = _base()
    data["scenes"][1] = {
        "type": "custom_sprite_path",
        "duration_s": 2.0,
        "params": {"sprite": "star", "path": [[0.1, 0.1], [2.0, 0.5]]},
    }
    with pytest.raises(ValidationError) as exc:
        validate_spec(data)
    assert "normalized" in str(exc.value)
