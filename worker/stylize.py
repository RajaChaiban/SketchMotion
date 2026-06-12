"""Overlay/stylize pipeline: real video -> per-frame sketch -> re-encode (audio preserved).

Performance: frames stream as raw RGB between two ffmpeg subprocesses and Python — **no PNG
files, no disk round-trips** (the old per-frame PNG extract/encode was the bottleneck). With
`workers > 1`, sketchify runs across processes in bounded-memory batches so a long clip never
holds all frames at once.

CLI:  uv run python -m worker.stylize in.mp4 out.mp4 --style ink|pencil [--seconds N] [--workers N]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
from PIL import Image

from render.sketch_filter import sketchify
from worker.video_ingest import probe


def _read_exact(stream, n: int) -> bytes:
    """Read exactly n bytes from a pipe (read() may return short)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _sketch_raw(buf: bytes, *, w: int, h: int, style: str, opts: dict) -> bytes:
    """Sketch one raw RGB frame -> raw RGB bytes (process-pool friendly: top-level, picklable)."""
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
    out = sketchify(Image.fromarray(arr, "RGB"), style=style, **opts)
    return out.tobytes()


def _decode_proc(input_path: Path, max_seconds: float | None, fps: float) -> subprocess.Popen:
    cmd = ["ffmpeg", "-v", "error"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-i", str(input_path), "-r", str(fps),  # resample to target fps (caps 60 -> 30)
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _encode_proc(output_path: Path, w: int, h: int, fps: float,
                 src: Path, has_audio: bool, max_seconds: float | None) -> subprocess.Popen:
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
    if has_audio:
        if max_seconds:
            cmd += ["-t", str(max_seconds)]
        cmd += ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "21",
            str(output_path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)


def stylize_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    style: str = "ink",
    max_seconds: float | None = None,
    fps: float | None = None,
    max_fps: float = 30.0,
    opts: dict | None = None,
    workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Stylize every frame of `input_path` -> `output_path` (original audio kept), streaming.

    Output fps is capped at `max_fps` (a hand-drawn sketch reads fine at 30 and it halves the
    work on 60fps sources). Frames stream as raw RGB; sketchify runs across threads (PIL/numpy
    release the GIL) so there is zero per-frame IPC.
    """
    info = probe(input_path)
    fps = fps or min(info["fps"], max_fps)
    w, h = info["width"], info["height"]
    opts = opts or {}
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame_size = w * h * 3
    total = max(1, round((max_seconds or info["duration"] or 0) * fps)) or 1

    dec = _decode_proc(Path(input_path), max_seconds, fps)
    enc = _encode_proc(out_path, w, h, fps, Path(input_path), info["has_audio"], max_seconds)

    def frames() -> Iterator[bytes]:
        while True:
            buf = _read_exact(dec.stdout, frame_size)
            if len(buf) < frame_size:
                break
            yield buf

    done = 0
    try:
        if workers and workers > 1:
            fn = partial(_sketch_raw, w=w, h=h, style=style, opts=opts)
            batch_size = workers * 4
            src = frames()
            with ThreadPoolExecutor(max_workers=workers) as ex:
                while True:
                    batch = list(islice(src, batch_size))  # bounds memory to ~batch_size frames
                    if not batch:
                        break
                    for out_buf in ex.map(fn, batch):
                        enc.stdin.write(out_buf)
                        done += 1
                        if on_progress:
                            on_progress(done, total)
        else:
            for buf in frames():
                enc.stdin.write(_sketch_raw(buf, w=w, h=h, style=style, opts=opts))
                done += 1
                if on_progress:
                    on_progress(done, total)
    finally:
        if dec.stdout:
            dec.stdout.close()
        if enc.stdin:
            enc.stdin.close()
        dec.wait()
        enc.wait()

    if enc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg encode failed (exit {enc.returncode})")
    if done == 0:
        raise ValueError("no frames decoded from input video")
    return str(out_path)


def stylize_image(input_path: str | Path, output_path: str | Path, *,
                  style: str = "ink", opts: dict | None = None) -> str:
    """Sketch a single image to a PNG (the 'just a sketch' / still output)."""
    img = Image.open(input_path)
    out = sketchify(img, style=style, **(opts or {})).convert("RGB")
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    return str(out_path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Turn a real video into a hand-drawn sketch.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--style", default="ink", choices=("ink", "pencil"))
    ap.add_argument("--seconds", type=float, default=None, help="only process the first N seconds")
    ap.add_argument("--workers", type=int, default=None, help="parallel frame workers")
    args = ap.parse_args(argv)

    def progress(done: int, total: int) -> None:
        if done == total or done % 50 == 0:
            print(f"  {done}/{total} frames", flush=True)

    path = stylize_video(args.input, args.output, style=args.style,
                         max_seconds=args.seconds, workers=args.workers, on_progress=progress)
    print(f"sketched -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
