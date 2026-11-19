"""端侧快路径模式测试：浏览器只上送 gloss，服务端负责造句 + TTS。"""

import asyncio
import json

import pytest

from realtime.protocol import parse_client_message
from realtime.server import AppState, Session


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_parse_gloss_message():
    msg = parse_client_message('{"type":"gloss","text":"头疼","score":0.93,"t":1}')
    assert msg["text"] == "头疼"
    assert msg["score"] == 0.93
    with pytest.raises(ValueError):
        parse_client_message('{"type":"gloss","text":"   "}')
    with pytest.raises(ValueError):
        parse_client_message('{"type":"control","action":"oops"}')


def test_edge_mode_gloss_to_sentence():
    state = AppState()
    state.matcher.clear()

    async def run():
        ws = FakeWS()
        session = Session(state, ws, scene_id="hospital")
        for word in ["我", "头疼", "两天"]:
            await session.handle_gloss({"type": "gloss", "text": word, "score": 0.9})
        await session.handle_control({"type": "control", "action": "sentence_end"})
        while session._tasks:
            await asyncio.gather(*list(session._tasks), return_exceptions=True)
        return ws.sent

    sent = asyncio.run(run())
    acks = [m for m in sent if m["type"] == "gloss_ack"]
    assert [a["text"] for a in acks] == ["我", "头疼", "两天"]
    sentences = [m for m in sent if m["type"] == "sentence"]
    assert sentences, [m["type"] for m in sent]
    assert sentences[0]["text"].startswith("医生")
    assert sentences[0]["stage"] == "fast"
    assert any(m["type"] == "audio" and m["url"].startswith("data:audio/wav") for m in sent)


def test_edge_mode_reset_drops_buffer():
    state = AppState()

    async def run():
        ws = FakeWS()
        session = Session(state, ws, scene_id="hospital")
        await session.handle_gloss({"type": "gloss", "text": "我", "score": 0.9})
        await session.handle_control({"type": "control", "action": "reset"})
        await session.handle_control({"type": "control", "action": "sentence_end"})
        while session._tasks:
            await asyncio.gather(*list(session._tasks), return_exceptions=True)
        return ws.sent

    sent = asyncio.run(run())
    assert not [m for m in sent if m["type"] == "sentence"]
