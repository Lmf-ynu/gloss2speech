"""服务端集成测试：REST + WebSocket 会话全链路（VAD -> 匹配 -> 造句 -> 语音事件）。"""

import asyncio
import json

import numpy as np
from fastapi.testclient import TestClient

from realtime.server import STATE, AppState, Session, _build_sequence, app


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def _frames(freq: float = 1.0, n: int = 26, noise: float = 0.0005, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    off_l = rng.uniform(-0.10, 0.10, (21, 2))
    off_r = rng.uniform(-0.10, 0.10, (21, 2))
    left0 = np.array([-0.45, 0.10], dtype=np.float32)
    right0 = np.array([0.45, 0.05], dtype=np.float32)
    out = []
    for t in range(n):
        s = float(np.sin(2 * np.pi * freq * t / 30.0))
        left = left0 + off_l + 0.12 * s
        right = right0 + off_r + 0.12 * s
        frame = np.concatenate([left, right]).astype(np.float32)
        frame = frame + rng.normal(0, noise, frame.shape).astype(np.float32)
        out.append(frame)
    return np.stack(out)


def _idle(n: int = 10, seed: int = 99) -> np.ndarray:
    rng = np.random.default_rng(seed)
    left0 = np.full((21, 2), [-0.45, 0.10], dtype=np.float32)
    right0 = np.full((21, 2), [0.45, 0.05], dtype=np.float32)
    base = np.concatenate([left0, right0])
    return np.stack([base + rng.normal(0, 0.0002, base.shape).astype(np.float32) for _ in range(n)])


def _stream_session(session: Session, frames: np.ndarray, t0: float = 0.0) -> float:
    """按 30fps 时间戳灌帧，返回结束时间。"""
    async def run() -> float:
        t = t0
        for frame in frames:
            t += 1000.0 / 30.0
            await session.handle_frame({"type": "frame", "t": t, "lm": frame.tolist()})
        # 等待慢路径任务（若已触发）
        while session._tasks:
            await asyncio.gather(*list(session._tasks), return_exceptions=True)
        return t

    return asyncio.run(run())


def test_health_and_scenes():
    client = TestClient(app)
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["tools"] >= 6
    scenes = client.get("/api/scenes").json()
    assert {s["id"] for s in scenes} >= {"hospital", "bank", "government"}


def test_compose_fast_endpoint():
    client = TestClient(app)
    res = client.post("/api/compose/fast", json={"words": ["我", "头疼", "两天"], "scene_id": "hospital"}).json()
    assert res["sentence"] == "医生，我头疼两天了。"
    assert res["latency_ms"] < 10


def test_template_record_endpoint():
    import shutil
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent.parent / "data"
    client = TestClient(app)
    frames = _frames(n=20).tolist()
    try:
        res = client.post("/api/templates/record", json={"label": "测试词", "speaker": "unit", "frames": frames}).json()
        assert res["ok"] is True
        assert res["frames"] == 20
        assert (data_dir / "templates.npz").exists()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_ws_session_end_to_end():
    state = AppState()
    state.matcher.clear()
    template_frames = _frames(freq=1.0, n=26, noise=0.0004, seed=0)
    state.matcher.add("头疼", _build_sequence(list(template_frames)), speaker="unit")

    async def run():
        ws = FakeWS()
        session = Session(state, ws, scene_id="hospital")
        session.normalizer = type(session.normalizer)("none")
        session.vad.config.start_energy = 0.006
        session.vad.config.stop_energy = 0.003
        session.vad.config.sentence_gap_ms = 600
        t = 0.0
        sequence = np.concatenate([
            _idle(10),
            _frames(freq=1.0, n=24, noise=0.0006, seed=0),
            _idle(30, seed=100),
        ])
        for frame in sequence:
            t += 1000.0 / 30.0
            await session.handle_frame({"type": "frame", "t": t, "lm": frame.tolist()})
        while session._tasks:
            await asyncio.gather(*list(session._tasks), return_exceptions=True)
        return ws.sent

    sent = asyncio.run(run())
    kinds = [m["type"] for m in sent]
    assert "vad" in kinds
    gloss = [m for m in sent if m["type"] == "gloss"]
    assert gloss and gloss[-1]["final"] is True
    assert gloss[-1]["text"] == "头疼", gloss[-1]
    sentences = [m for m in sent if m["type"] == "sentence"]
    assert sentences, kinds
    assert any(s["stage"] == "fast" for s in sentences)
    audios = [m for m in sent if m["type"] == "audio"]
    assert audios and audios[0]["url"].startswith("data:audio/wav;base64,")


def test_translate_endpoint_returns_events():
    client = TestClient(app)
    res = client.post("/api/translate", json={"words": ["我", "头疼", "两天"], "scene_id": "hospital"}).json()
    assert res["fast_text"].startswith("医生")
    assert res["fast_ms"] < 50
    assert any(e["kind"] == "sentence" and e["stage"] == "fast" for e in res["events"])
    assert res["total_ms"] < 1500


def test_metrics_endpoint():
    client = TestClient(app)
    data = client.get("/api/metrics").json()
    assert "latency" in data and "tools" in data and "tool_specs" in data
    names = {t["name"] for t in data["tool_specs"]}
    assert {"compose.template", "compose.cot", "tts.stream", "emergency.match"} <= names
