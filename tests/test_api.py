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
