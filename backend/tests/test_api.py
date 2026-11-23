"""后端接口与安全护栏测试（MOCK_MODE=1，无需任何云服务密钥）。"""

from __future__ import annotations

import base64
import json
import os

os.environ["MOCK_MODE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services.llm import (  # noqa: E402
    EMERGENCY_TEXT,
    _DualModeSentenceStreamer,
    _parse_compose_output,
    guard_compose,
)
from backend.services.sis import _build_asr_request, _build_tts_request  # noqa: E402
from backend.services.vision import VisionService  # noqa: E402

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


def test_mock_recognize_alternatives_is_2d_list():
    image = base64.b64encode(b"fake-jpeg-bytes").decode()
    resp = client.post("/api/recognize", json={"image": image, "scene_id": "hospital"})
    assert resp.status_code == 200
    alternatives = resp.json()["alternatives"]
    assert isinstance(alternatives, list)
    assert len(alternatives) >= 1
    assert all(isinstance(group, list) and all(isinstance(w, str) for w in group) for group in alternatives)


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


def test_compose_emergency_bypass():
    with client.stream(
        "POST",
        "/api/compose",
        json={"words": ["我", "急救"], "scene_id": "hospital"},
    ) as resp:
        assert resp.status_code == 200
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines if line != "data: [DONE]"]
    done_events = [ev for ev in events if ev.get("type") == "done"]
    assert len(done_events) == 1
    done = done_events[0]
    assert done["emergency"] is True
    assert done["sentence"] == EMERGENCY_TEXT
    assert done["guard_triggered"] is False


def test_medical_guardrail_blocks_hallucination_and_missing_words():
    # 1) 核心词遗漏：缺少"两天"
    fixed, triggered, reason = guard_compose(["我", "头疼", "两天"], "医生，我头疼。", "hospital")
    assert triggered is True
    assert "遗漏" in reason
    assert fixed == "医生，我头疼两天了。"

    # 2) 医疗幻觉：未提及"青霉素"/"药物"
    fixed, triggered, reason = guard_compose(["过敏"], "医生，我对青霉素药物过敏。", "hospital")
    assert triggered is True
    assert "拦截未提及医疗信息" in reason
    assert fixed == "医生，我过敏了。"

    # 3) 对话历史出现过的词允许保留
    ok_sentence, ok_triggered, ok_reason = guard_compose(
        ["过敏"],
        "医生，我青霉素过敏。",
        "hospital",
        history=[{"role": "other", "text": "您对青霉素过敏吗？"}],
    )
    assert ok_triggered is False
    assert ok_reason == ""
    assert ok_sentence == "医生，我青霉素过敏。"

    # 4) 过度扩写拦截
    fixed, triggered, reason = guard_compose(
        ["头疼"],
        "医生，我今天早上起床之后头疼得特别厉害并且伴随持续加重建议立刻检查治疗。",
        "hospital",
    )
    assert triggered is True
    assert "过度扩写" in reason
    assert fixed == "医生，我头疼。"


def test_dual_mode_sentence_streamer_and_parser():
    # 格式 1：首行纯文本 + 次行 JSON
    samples_line = ["医生，我", "头疼两天了。\n", '{"sentence":"医生，我头疼两天了。","alternative":"我头疼两天。","need_confirm":true}']
    streamer1 = _DualModeSentenceStreamer()
    out1 = "".join(streamer1.feed(chunk) for chunk in samples_line)
    assert out1 == "医生，我头疼两天了。"
    parsed1 = _parse_compose_output("".join(samples_line), ["我", "头疼", "两天"])
    assert parsed1["sentence"] == "医生，我头疼两天了。"
    assert parsed1["alternative"] == "我头疼两天。"
    assert parsed1["need_confirm"] is True

    # 格式 2：纯 JSON 且 alternative 在 sentence 之前
    raw2 = '{"alternative":"我头疼。","sentence":"医生，我头疼两天了。","need_confirm":false}'
    streamer2 = _DualModeSentenceStreamer()
    out2 = "".join(streamer2.feed(raw2[i : i + 5]) for i in range(0, len(raw2), 5))
    assert out2 == "医生，我头疼两天了。"
    assert _parse_compose_output(raw2, ["头疼"])["sentence"] == "医生，我头疼两天了。"

    # 格式 3：带 markdown 代码块
    raw3 = '```json\n{"sentence":"医生，我发烧了。","alternative":"我发烧了。"}\n```'
    streamer3 = _DualModeSentenceStreamer()
    out3 = "".join(streamer3.feed(raw3[i : i + 4]) for i in range(0, len(raw3), 4))
    assert out3 == "医生，我发烧了。"
    assert _parse_compose_output(raw3, ["发烧"])["sentence"] == "医生，我发烧了。"


def test_vision_validate_against_vocab():
    scene = {"id": "hospital", "name": "医院就诊", "vocab": [{"word": "头疼"}, {"word": "发烧"}]}
    validated = VisionService._validate_against_vocab(
        {"words": ["头疼", "词表外词"], "confidence": 0.92, "alternatives": [["发烧"]]},
        scene,
    )
    assert validated["words"] == ["头疼"]
    assert validated["confidence"] <= 0.45
    assert ["词表外词"] in validated["alternatives"]


def test_sis_sdk_request_builders():
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdksis.v1 import SisClient
    from huaweicloudsdksis.v1.region.sis_region import SisRegion

    tts_req = _build_tts_request("你好")
    assert tts_req.body.text == "你好"
    assert tts_req.body.config.audio_format == "wav"
    assert tts_req.body.config.sample_rate == "16000"
    assert tts_req.body.config._property == settings.sis_tts_voice

    asr_req = _build_asr_request("ZmFrZQ==")
    assert asr_req.body.data == "ZmFrZQ=="
    assert asr_req.body.config.audio_format == "wav"
    assert asr_req.body.config._property == settings.sis_asr_property
    assert asr_req.body.config.add_punc == "yes"

    client_built = (
        SisClient.new_builder()
        .with_credentials(BasicCredentials("ak", "sk", "proj"))
        .with_region(SisRegion.value_of("cn-north-4"))
        .build()
    )
    assert client_built is not None


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
