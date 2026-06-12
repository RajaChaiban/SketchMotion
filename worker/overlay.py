"""Composite an OverlaySpec's annotations onto a base video (audio preserved).

Streams raw RGB frames (same pipe pattern as stylize) and only renders/blends an overlay on
frames inside an annotation window — everything else passes through untouched, so cost scales
with how much is annotated, not clip length.

CLI:  uv run python -m worker.overlay base.mp4 annotations.json out.mp4
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import numpy as np

from render.overlay_engine import active_at, render_overlay_frame
from worker.overlay_spec import OverlaySpec, validate_overlay_spec
from worker.stylize import _decode_proc, _encode_proc, _read_exact
from worker.video_ingest import probe


def composite_annotations(
    base_path: str | Path,
    spec: OverlaySpec,
    output_path: str | Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    info = probe(base_path)
    w, h, fps = info["width"], info["height"], info["fps"]
    annotations = spec.annotations
    frame_size = w * h * 3
    total = max(1, round((info["duration"] or 0) * fps)) or 1
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dec = _decode_proc(Path(base_path), None, fps)  # -r fps == passthrough
    enc = _encode_proc(out_path, w, h, fps, Path(base_path), info["has_audio"], None)

    idx = 0
    try:
        while True:
            buf = _read_exact(dec.stdout, frame_size)
            if len(buf) < frame_size:
                break
            t = idx / fps
            act = active_at(annotations, t)
            if act:
                base = np.frombuffer(buf, np.uint8).reshape(h, w, 3).astype(np.float32)
                ov = np.asarray(render_overlay_frame(act, t, (w, h)), np.float32)  # (h,w,4)
                alpha = ov[..., 3:4] / 255.0
                out = base * (1.0 - alpha) + ov[..., :3] * alpha
                enc.stdin.write(np.clip(out, 0, 255).astype(np.uint8).tobytes())
            else:
                enc.stdin.write(buf)  # passthrough — no allocation
            idx += 1
            if on_progress:
                on_progress(idx, total)
    finally:
        if dec.stdout:
            dec.stdout.close()
        if enc.stdin:
            enc.stdin.close()
        dec.wait()
        enc.wait()

    if enc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg encode failed (exit {enc.returncode})")
    if idx == 0:
        raise ValueError("no frames decoded from base video")
    return str(out_path)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: python -m worker.overlay <base.mp4> <annotations.json> <out.mp4>",
              file=sys.stderr)
        return 2
    base, ann_path, out = argv[0], argv[1], argv[2]
    data = json.loads(Path(ann_path).read_text(encoding="utf-8"))
    info = probe(base)
    data.setdefault("source_fps", info["fps"])
    data.setdefault("source_resolution", [info["width"], info["height"]])
    spec = validate_overlay_spec(data)

    def progress(done: int, total: int) -> None:
        if done == total or done % 50 == 0:
            print(f"  {done}/{total} frames", flush=True)

    path = composite_annotations(base, spec, out, on_progress=progress)
    print(f"annotated -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
