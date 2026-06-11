"""End-to-end pipeline: refine -> compile -> render, with status transitions.
Uses the FakeQueue (no Redis) and writes a real MP4 (needs ffmpeg)."""
from __future__ import annotations

import json
import shutil

import pytest

from app.config import Settings
from tests.conftest import FakeQueue
from worker.gemini_client import ImageBrief, stub_compile
from worker.pipeline import run_generate

HAS_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def _payload(tmp_path, **over) -> dict:
    p = {
        "job_id": "job-test",
        "prompt": "celebrate a product launch",
        "duration_s": 5,
        "aspect": "16:9",
        "draft": True,  # 12 fps -> fast render
        "image_path": None,
        "image_mime": None,
    }
    p.update(over)
    return p


async def test_stub_path_produces_mp4_and_done(tmp_path):
    q = FakeQueue()
    settings = Settings(gemini_api_key="", llm_provider="stub", output_dir=str(tmp_path))
    result = await run_generate(q, settings, _payload(tmp_path))

    out = tmp_path / "job-test.mp4"
    assert out.exists() and out.stat().st_size > 0
    assert result["output"].endswith("job-test.mp4")
    assert q.jobs["job-test"]["status"] == "done"
    assert q.jobs["job-test"]["progress_pct"] == 100
    # the compiled spec was stored for /jobs/{id}/spec
    spec = json.loads(q.jobs["job-test"]["spec"])
    assert spec["scenes"][0]["type"] in {"hook_claim", "hook_question", "pattern_interrupt"}


async def test_rejected_prompt_marks_failed(tmp_path):
    q = FakeQueue()
    settings = Settings(gemini_api_key="", llm_provider="stub", output_dir=str(tmp_path))
    await run_generate(q, settings, _payload(tmp_path, prompt="make Batman fly"))
    assert q.jobs["job-test"]["status"] == "failed"
    assert "batman" in q.jobs["job-test"]["error"].lower()
    assert not (tmp_path / "job-test.mp4").exists()


class _FakeProvider:
    """Stands in for a vision-capable LLM provider (e.g. Gemini)."""

    name = "fake"
    supports_vision = True

    def __init__(self):
        self.vision_called = False

    def vision(self, data: bytes, mime: str) -> ImageBrief:
        self.vision_called = True
        return ImageBrief(subject="rocket", suggested_sprites=["rocket"])

    def compile_spec(self, *, refined_prompt, target_duration_s, aspect, image_brief, skill=""):
        # exercise the vision-fed compile branch deterministically
        assert image_brief is not None and image_brief.subject == "rocket"
        return stub_compile(refined_prompt, target_duration_s, aspect)


async def test_vision_provider_branch_with_image(tmp_path):
    img = tmp_path / "logo.png"
    img.write_bytes(b"\x89PNG fake-bytes")
    q = FakeQueue()
    settings = Settings(gemini_api_key="a-key", output_dir=str(tmp_path))
    fake = _FakeProvider()
    await run_generate(
        q, settings,
        _payload(tmp_path, image_path=str(img), image_mime="image/png"),
        provider=fake,
    )
    assert fake.vision_called
    assert q.jobs["job-test"]["status"] == "done"
    assert (tmp_path / "job-test.mp4").exists()


async def test_status_progression_recorded(tmp_path):
    q = FakeQueue()
    settings = Settings(gemini_api_key="", llm_provider="stub", output_dir=str(tmp_path))
    await run_generate(q, settings, _payload(tmp_path))
    # final state is terminal; spec + progress set along the way
    assert q.jobs["job-test"]["status"] == "done"
    assert "spec" in q.jobs["job-test"]
