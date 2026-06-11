"""Video ingest helpers for overlay/stylize mode — ffprobe metadata + frame extraction.

Thin wrappers over the system ffmpeg/ffprobe binaries (invoked via subprocess with UTF-8
decoding — Windows cp1252 would choke on some metadata).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[:300]}")
    return proc.stdout


def _parse_fps(value: str | None) -> float:
    if not value or value in ("0/0", "N/A"):
        return 30.0
    if "/" in value:
        num, den = value.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 30.0
    return float(value)


def probe(path: str | Path) -> dict:
    """Return {width, height, fps, duration, has_audio} for a video file."""
    out = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("no video stream found")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "duration": duration,
        "has_audio": has_audio,
    }


def extract_frames(path: str | Path, out_dir: str | Path, limit_seconds: float | None = None) -> list[Path]:
    """Decode the video to numbered PNG frames in out_dir; returns the frame paths in order."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if limit_seconds:
        cmd += ["-t", str(limit_seconds)]
    cmd += ["-i", str(path), str(out_dir / "f%06d.png")]
    _run(cmd)
    return sorted(out_dir.glob("f*.png"))
