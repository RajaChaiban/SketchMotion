"""Phase 1 endpoint contract tests (no Redis, no Gemini, no renderer)."""
from __future__ import annotations

import io

import pytest


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_index_served(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "SketchMotion" in r.text


async def test_generate_enqueues(client, fake_queue):
    r = await client.post(
        "/generate",
        data={"prompt": "celebrate a launch", "duration_s": "15", "aspect": "16:9"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    # the job was actually handed to the queue with the right payload
    assert len(fake_queue.enqueued) == 1
    assert fake_queue.enqueued[0]["prompt"] == "celebrate a launch"
    assert fake_queue.enqueued[0]["duration_s"] == 15


async def test_generate_with_image(client, fake_queue):
    files = {"image": ("logo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")}
    r = await client.post(
        "/generate",
        data={"prompt": "logo reveal", "duration_s": "10", "aspect": "1:1"},
        files=files,
    )
    assert r.status_code == 200
    assert fake_queue.enqueued[0]["image_path"] is not None
    assert fake_queue.enqueued[0]["image_mime"] == "image/png"


@pytest.mark.parametrize(
    "data,detail_contains",
    [
        ({"prompt": "  ", "duration_s": "15", "aspect": "16:9"}, "required"),
        ({"prompt": "x", "duration_s": "3", "aspect": "16:9"}, "duration_s"),
        ({"prompt": "x", "duration_s": "70", "aspect": "16:9"}, "duration_s"),
        ({"prompt": "x", "duration_s": "15", "aspect": "4:3"}, "aspect"),
    ],
)
async def test_generate_validation(client, data, detail_contains):
    r = await client.post("/generate", data=data)
    assert r.status_code == 422
    assert detail_contains in r.json()["detail"]


async def test_generate_rejects_non_image(client):
    files = {"image": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    r = await client.post(
        "/generate",
        data={"prompt": "x", "duration_s": "15", "aspect": "16:9"},
        files=files,
    )
    assert r.status_code == 422
    assert "image" in r.json()["detail"]


async def test_job_status_lifecycle(client, fake_queue):
    r = await client.post(
        "/generate", data={"prompt": "hello", "duration_s": "15", "aspect": "16:9"}
    )
    job_id = r.json()["job_id"]
    s = await client.get(f"/jobs/{job_id}")
    assert s.status_code == 200
    assert s.json()["status"] == "queued"

    await fake_queue.set_status(job_id, status="done", progress_pct=100)
    s2 = await client.get(f"/jobs/{job_id}")
    assert s2.json()["status"] == "done"
    assert s2.json()["progress_pct"] == 100


async def test_job_status_404(client):
    r = await client.get("/jobs/does-not-exist")
    assert r.status_code == 404


async def test_video_404_before_render(client):
    r = await client.get("/jobs/whatever/video")
    assert r.status_code == 404


async def test_stylize_enqueues(client, fake_queue):
    files = {"video": ("clip.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"0" * 128), "video/mp4")}
    r = await client.post("/stylize", data={"style": "ink"}, files=files)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    assert fake_queue.enqueued[0]["style"] == "ink"
    assert fake_queue.enqueued[0]["video_path"].endswith("_src")


async def test_stylize_rejects_bad_style(client):
    files = {"video": ("clip.mp4", io.BytesIO(b"data"), "video/mp4")}
    r = await client.post("/stylize", data={"style": "watercolor"}, files=files)
    assert r.status_code == 422
    assert "style" in r.json()["detail"]


async def test_stylize_rejects_non_video(client):
    files = {"video": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    r = await client.post("/stylize", data={"style": "ink"}, files=files)
    assert r.status_code == 422
    assert "video" in r.json()["detail"]


# --- unified /create intake ---

async def test_create_auto_defers_to_intake_job(client, fake_queue):
    """Default mode=auto routes through the AI agent (intake_job)."""
    r = await client.post("/create", data={"prompt": "celebrate a launch", "duration_s": "15"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "animate" and body["output_kind"] == "video"  # provisional
    assert fake_queue.enqueued[0]["function"] == "intake_job"
    assert fake_queue.enqueued[0]["prompt"] == "celebrate a launch"
    assert fake_queue.enqueued[0]["options"]["duration_s"] == 15


async def test_create_auto_image_provisional_still(client, fake_queue):
    files = {"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")}
    r = await client.post("/create", data={"style": "pencil"}, files=files)
    assert r.status_code == 200
    assert r.json()["route"] == "stylize_image" and r.json()["output_kind"] == "still"
    assert fake_queue.enqueued[0]["function"] == "intake_job"
    assert fake_queue.enqueued[0]["file_kind"] == "image"


async def test_create_explicit_animate_routes_direct(client, fake_queue):
    r = await client.post("/create", data={"prompt": "a recap", "mode": "animate", "duration_s": "12"})
    assert r.status_code == 200
    assert r.json()["route"] == "animate"
    assert fake_queue.enqueued[0]["function"] == "generate_job"  # direct, no agent hop
    assert fake_queue.enqueued[0]["prompt"] == "a recap"


async def test_annotate_enqueues(client, fake_queue):
    import json
    anns = json.dumps([
        {"type": "circle_highlight", "t_start": 1.0, "t_end": 2.0, "anchor": [0.4, 0.4, 0.2, 0.2]},
        {"type": "writeon_caption", "t_start": 1.0, "t_end": 3.0, "anchor": [0.1, 0.8, 0.8, 0.1],
         "params": {"text": "GAME WINNER"}},
    ])
    files = {"video": ("clip.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64), "video/mp4")}
    r = await client.post("/annotate", data={"annotations": anns}, files=files)
    assert r.status_code == 200
    assert fake_queue.enqueued[0]["function"] == "annotate_job"
    assert len(fake_queue.enqueued[0]["annotations"]) == 2


async def test_annotate_rejects_bad_geometry(client):
    import json
    anns = json.dumps([{"type": "box_outline", "t_start": 1.0, "t_end": 2.0, "anchor": [0.9, 0.4, 0.5, 0.2]}])
    files = {"video": ("clip.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), "video/mp4")}
    r = await client.post("/annotate", data={"annotations": anns}, files=files)
    assert r.status_code == 422
    assert "annotations" in r.json()["detail"]


async def test_create_explicit_stylize_video_routes_direct(client, fake_queue):
    files = {"file": ("clip.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64), "video/mp4")}
    r = await client.post("/create", data={"mode": "stylize", "style": "ink"}, files=files)
    assert r.status_code == 200
    assert r.json()["route"] == "stylize_video"
    assert fake_queue.enqueued[0]["function"] == "stylize_job"
    assert fake_queue.enqueued[0]["style"] == "ink"


async def test_create_requires_some_input(client):
    r = await client.post("/create", data={"prompt": "   "})
    assert r.status_code == 422


async def test_create_rejects_bad_file(client):
    files = {"file": ("notes.txt", io.BytesIO(b"hello there"), "text/plain")}
    r = await client.post("/create", data={"prompt": ""}, files=files)
    assert r.status_code == 422
