"""Compile several varied prompts via the configured LLM provider and render each.
Run: uv run python -m scripts.batch_demo
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from render.engine import render_spec
from worker.llm import get_provider

PROMPTS = [
    ("5 simple ways to save money on groceries", 18, "16:9"),
    ("how to stay focused while working from home", 15, "9:16"),
    ("introducing our new small-batch coffee subscription", 12, "1:1"),
    ("the science of why a short walk clears your mind", 16, "9:16"),
]


def main() -> None:
    settings = Settings(llm_provider="claude_cli")
    provider = get_provider(settings)
    print(f"provider={provider.name} model={settings.claude_cli_model}", flush=True)
    Path("outputs").mkdir(exist_ok=True)

    for i, (prompt, dur, aspect) in enumerate(PROMPTS):
        print(f"\n=== [{i}] {prompt!r}  ({dur}s {aspect}) ===", flush=True)
        try:
            spec = provider.compile_spec(refined_prompt=prompt, target_duration_s=dur, aspect=aspect)
        except Exception as e:  # noqa: BLE001
            print(f"  COMPILE FAILED: {type(e).__name__}: {e}", flush=True)
            continue
        print(f"  title: {spec.title}", flush=True)
        print(f"  total: {spec.total_duration_s}s  scenes: {len(spec.scenes)}", flush=True)
        for sc in spec.scenes:
            print(f"    {sc.duration_s:>4}s  {sc.type:16} {sc.params}", flush=True)
        out = f"outputs/demo_{i}.mp4"
        render_spec(spec.model_dump(), out)
        print(f"  rendered -> {out}", flush=True)

    print("\nBATCH DONE", flush=True)


if __name__ == "__main__":
    main()
