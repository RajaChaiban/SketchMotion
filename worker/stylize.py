"""Overlay/stylize pipeline: real video -> per-frame sketch -> re-encode (audio preserved).

This is the no-LLM half of Phase 7 — it turns the players themselves into a sketch.
(The LLM half — detecting key moments and compositing arrows/callouts — is deferred until a
video-capable model key is configured; it will layer on top of these same frames.)

CLI:  uv run python -m worker.stylize in.mp4 out.mp4 --style ink|pencil [--seconds N] [--workers N]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image

from render.sketch_filter import sketchify
from worker.video_ingest import extract_frames, probe


def _stylize_frame_file(task: tuple[str, str, str, dict]) -> str:
    in_path, out_path, style, opts = task
    img = Image.open(in_path)
    sketchify(img, style=style, **opts).save(out_path)
    return out_path


def _encode(frames_dir: Path, fps: float, src: Path, out_path: Path, has_audio: bool) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps), "-i", str(frames_dir / "f%06d.png"),
    ]
    if has_audio:
        cmd += ["-i", str(src)]
    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(out_path)]
    subprocess.run(cmd, check=True)


def stylize_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    style: str = "ink",
    max_seconds: float | None = None,
    fps: float | None = None,
    opts: dict | None = None,
    workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Stylize every frame of `input_path` and encode to `output_path` (original audio kept)."""
    info = probe(input_path)
    fps = fps or info["fps"]
    opts = opts or {}

    in_tmp = Path(tempfile.mkdtemp(prefix="sm_in_"))
    out_tmp = Path(tempfile.mkdtemp(prefix="sm_out_"))
    try:
        frames = extract_frames(input_path, in_tmp, limit_seconds=max_seconds)
        if not frames:
            raise ValueError("no frames extracted from input video")
        total = len(frames)
        tasks = [(str(fp), str(out_tmp / fp.name), style, opts) for fp in frames]

        if workers and workers > 1:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=workers) as ex:
                for i, _ in enumerate(ex.map(_stylize_frame_file, tasks)):
                    if on_progress:
                        on_progress(i + 1, total)
        else:
            for i, task in enumerate(tasks):
                _stylize_frame_file(task)
                if on_progress:
                    on_progress(i + 1, total)

        _encode(out_tmp, fps, Path(input_path), Path(output_path), info["has_audio"])
    finally:
        shutil.rmtree(in_tmp, ignore_errors=True)
        shutil.rmtree(out_tmp, ignore_errors=True)
    return str(output_path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Turn a real video into a hand-drawn sketch.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--style", default="ink", choices=("ink", "pencil"))
    ap.add_argument("--seconds", type=float, default=None, help="only process the first N seconds")
    ap.add_argument("--workers", type=int, default=None, help="parallel frame workers")
    args = ap.parse_args(argv)

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  {done}/{total} frames", flush=True)

    path = stylize_video(
        args.input, args.output, style=args.style,
        max_seconds=args.seconds, workers=args.workers, on_progress=progress,
    )
    print(f"sketched -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
