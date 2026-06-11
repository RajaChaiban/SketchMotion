"""Video stylize pipeline: ingest probe + full real-video -> sketch (audio preserved).
Generates tiny test videos with ffmpeg's lavfi sources."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from worker.stylize import stylize_video
from worker.video_ingest import _parse_fps, probe

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _make_video(path, *, with_audio: bool, dur=1, size="160x120", rate=10) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)


def _has_audio_stream(path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    return "audio" in out


def test_parse_fps():
    assert _parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _parse_fps("25") == 25.0
    assert _parse_fps("0/0") == 30.0
    assert _parse_fps(None) == 30.0


def test_probe_reports_metadata(tmp_path):
    src = tmp_path / "in.mp4"
    _make_video(src, with_audio=True)
    info = probe(src)
    assert info["width"] == 160 and info["height"] == 120
    assert info["has_audio"] is True
    assert info["duration"] > 0


@pytest.mark.parametrize("style", ["ink", "pencil"])
def test_stylize_produces_h264(tmp_path, style):
    src = tmp_path / "in.mp4"
    out = tmp_path / f"out_{style}.mp4"
    _make_video(src, with_audio=False)
    stylize_video(src, out, style=style)
    assert out.exists() and out.stat().st_size > 0
    probe_out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    ).stdout
    assert "h264" in probe_out


def test_stylize_preserves_audio(tmp_path):
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    _make_video(src, with_audio=True)
    stylize_video(src, out, style="ink")
    assert _has_audio_stream(out)


@pytest.mark.parametrize("style", ["ink", "pencil"])
def test_stylize_image_writes_png(tmp_path, style):
    import numpy as np
    from PIL import Image

    from worker.skills import style_opts
    from worker.stylize import stylize_image

    src = tmp_path / "in.png"
    xx, yy = np.meshgrid(np.linspace(0, 255, 80), np.linspace(0, 255, 60))
    Image.fromarray(np.stack([xx, yy, (xx + yy) / 2], -1).astype("uint8"), "RGB").save(src)
    out = tmp_path / "out.png"
    stylize_image(src, out, style=style, opts=style_opts(style))
    assert out.exists()
    assert Image.open(out).size == (80, 60)


def test_stylize_reports_progress(tmp_path):
    src = tmp_path / "in.mp4"
    _make_video(src, with_audio=False, dur=1, rate=8)
    seen = []
    stylize_video(src, tmp_path / "o.mp4", style="ink", on_progress=lambda d, t: seen.append((d, t)))
    assert seen and seen[-1][0] == seen[-1][1]  # ends at done==total
