"""后端接口测试（MOCK_MODE=1，无需任何云服务密钥）。"""

from __future__ import annotations

import base64
import json
import os

os.environ["MOCK_MODE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

client = TestClient(app)


def test_scenes():
    resp = client.get("/api/scenes")
    assert resp.status_code == 200
    scenes = resp.json()
    assert len(scenes) == 4
    hospital = next(scene for scene in scenes if scene["id"] == "hospital")
    assert len(hospital["vocab"]) >= 20
    assert all("word" in item and "desc" in item for item in hospital["vocab"])


def test_health_reports_service_modes():
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert data["services"]["vision"] == "mock"
    assert data["services"]["llm"] == "mock"
    assert data["services"]["sis"] == "mock"


def test_recognize_returns_vocab_word():
    image = base64.b64encode(b"fake-jpeg-bytes").decode()
    resp = client.post("/api/recognize", json={"image": image, "scene_id": "hospital"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["words"]
    assert 0.0 <= data["confidence"] <= 1.0


def test_recognize_rejects_empty_image():
    resp = client.post("/api/recognize", json={"image": "   ", "scene_id": "hospital"})
    assert resp.status_code == 400


def test_compose_streams_sentence():
    with client.stream(
        "POST",
        "/api/compose",
        json={"words": ["我", "头疼", "两天"], "scene_id": "hospital"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "data:" in body
    assert "医生，我头疼两天了" in body
    assert "[DONE]" in body


def test_tts_returns_playable_wav():
    resp = client.post("/api/tts", json={"text": "我头疼两天了"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "wav"
    raw = base64.b64decode(data["audio"])
    assert raw[:4] == b"RIFF"


def test_asr_returns_text():
    audio = base64.b64encode(b"fake-wav").decode()
    resp = client.post("/api/asr", json={"audio": audio})
    assert resp.status_code == 200
    assert resp.json()["text"]


def test_quick_reply_returns_three():
    resp = client.post(
        "/api/quick-reply",
        json={"text": "你哪里不舒服？", "scene_id": "hospital"},
    )
    assert resp.status_code == 200
    replies = resp.json()["replies"]
    assert 1 <= len(replies) <= 3
    assert any("头疼" in reply for reply in replies)
