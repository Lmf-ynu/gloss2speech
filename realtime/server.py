"""FastAPI 网关：浏览器的骨骼流 -> 结构化事件流（快路径 + 智能体慢路径）。

启动（项目根目录）：
    uvicorn realtime.server:app --host 127.0.0.1 --port 8000 --reload

接口：
    WS   /ws/stream                骨骼流上行，事件下行（协议见 protocol.py）
    GET  /api/health               存活探针
    GET  /api/scenes               场景 + 词表
    POST /api/templates/record     录入一个手语词模板（零训练）
    POST /api/compose/fast         模板速成句（< 10ms）
    POST /api/translate            完整链路（用于 Postman 联调，非流式）
    GET  /api/metrics              L1–L4 延迟分位 + 工具调用统计
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agentlayer.orchestrator import Event as AgentEvent
from agentlayer.orchestrator import Orchestrator
from agentlayer.providers import MockLLMProvider, MockTTSProvider
from agentlayer.tools import SCENES, build_registry, compose_template

from .landmarks import FeatureExtractor, Normalizer
from .matcher import DTWMatcher
from .protocol import (
    AudioEvent,
    ErrorEvent,
    GlossEvent,
    Latency,
    SentenceEvent,
    VadEvent,
    now_ms,
    parse_client_message,
)
from .vad import MotionVAD, VADConfig

DATA_DIR = Path(__file__).resolve().parent / "data"
UI_FRAME_MS = 16.7  # 一次浏览器渲染的预算


# --------------------------------------------------------------------------- 指标


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), p))


class Metrics:
    def __init__(self, limit: int = 500) -> None:
        self._data: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=limit))

    def record(self, tag: str, value: float) -> None:
        self._data[tag].append(float(value))

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for tag, values in self._data.items():
            vals = list(values)
            out[tag] = {
                "n": len(vals),
                "p50": round(_pct(vals, 50), 1),
                "p95": round(_pct(vals, 95), 1),
                "max": round(max(vals), 1) if vals else 0.0,
            }
        return out


# --------------------------------------------------------------------------- 状态


def _build_sequence(frames: list[Any]) -> np.ndarray:
    """REST 录入模板：原始关键点帧 -> 归一化特征序列。"""
    normalizer = Normalizer("auto")
    extractor = FeatureExtractor()
    feats: list[np.ndarray] = []
    for i, frame in enumerate(frames):
        if i == 0:
            extractor.reset()
        points = normalizer.points(frame)
        feats.append(extractor(normalizer.embed(points)))
    return np.stack(feats)


class AppState:
    def __init__(self) -> None:
        self.matcher = DTWMatcher(length=24, coarse_k=8)
        self.metrics = Metrics()
        self.registry = build_registry(MockLLMProvider(), MockTTSProvider())
        self.orchestrator = Orchestrator(self.registry)
        self.started_at = now_ms()
        self._load_templates()

    # ---- 模板持久化（零训练的关键：录入即用、重启不丢） ----
    def _load_templates(self) -> None:
        meta_path = DATA_DIR / "templates.json"
        npz_path = DATA_DIR / "templates.npz"
        if not meta_path.exists() or not npz_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            data = np.load(npz_path)
            for i, item in enumerate(meta):
                key = f"t{i}"
                if key in data:
                    self.matcher.add(item["label"], data[key], item.get("speaker", ""))
        except Exception as exc:  # noqa: BLE001 - 模板损坏不应阻止服务启动
            print(f"[warn] 模板加载失败，忽略：{exc}")

    def _save_templates(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        items = self.matcher.export()
        meta = [{"label": label, "speaker": speaker} for label, speaker, _ in items]
        (DATA_DIR / "templates.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        np.savez_compressed(DATA_DIR / "templates.npz", **{f"t{i}": seq for i, (_, _, seq) in enumerate(items)})

    def record_template(self, label: str, frames: list[Any], speaker: str = "") -> dict[str, Any]:
        sequence = _build_sequence(frames)
        self.matcher.add(label, sequence, speaker=speaker)
        self._save_templates()
        return {"label": label, "frames": int(sequence.shape[0]), "templates": self.matcher.size}


STATE = AppState()

app = FastAPI(title="SignBridge Realtime", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- WS 会话


class Session:
    def __init__(self, state: AppState, ws: WebSocket, scene_id: str = "hospital") -> None:
        self.state = state
        self.ws = ws
        self.scene_id = scene_id
        self.normalizer = Normalizer("auto")
        self.extractor = FeatureExtractor()
        self.vad = MotionVAD(VADConfig())
        self.matcher = state.matcher
        self.buffer: list[np.ndarray] = []
        self.words: list[str] = []
        self._trace = 0
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._last_active_wall = time.perf_counter()

    def _tid(self) -> str:
        self._trace += 1
        return f"tr-{self._trace}"

    async def send(self, payload: str) -> None:
        async with self._lock:
            await self.ws.send_text(payload)

    async def _error(self, code: str, message: str) -> None:
        await self.send(ErrorEvent(self._tid(), now_ms(), code, message).to_json())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    # ---- 帧处理（这是热路径，必须保持 < 15ms） ----
    async def handle_frame(self, msg: dict[str, Any]) -> None:
        t_client = float(msg.get("t") or now_ms())
        try:
            points = self.normalizer.points(msg["lm"], msg.get("pose"))
        except Exception as exc:  # noqa: BLE001
            await self._error("bad_frame", str(exc))
            return
        embed = self.normalizer.embed(points)
        step = self.vad.step(points, t_client)
        if step.energy > self.vad.config.start_energy:
            self._last_active_wall = time.perf_counter()

        if step.action == "start":
            self.extractor.reset()
            self.buffer = []
            await self.send(VadEvent(self._tid(), now_ms(), "SIGNING", step.segment_id).to_json())
        if step.state in {"SIGNING", "COMMITTING"}:
            self.buffer.append(self.extractor(embed))

        if step.action == "incremental" and len(self.buffer) >= 6:
            started = time.perf_counter()
            topk, conf, _ = self.matcher.match(np.stack(self.buffer))
            match_ms = (time.perf_counter() - started) * 1000.0
            if topk and conf >= 0.30:
                await self.send(
                    GlossEvent(
                        trace_id=self._tid(), t=now_ms(), text=topk[0].text,
                        score=round(conf, 3), final=False, topk=topk,
                        latency=Latency(match=int(match_ms)),
                    ).to_json()
                )
        elif step.action == "end" and len(self.buffer) >= 4:
            started = time.perf_counter()
            topk, conf, _ = self.matcher.match(np.stack(self.buffer))
            match_ms = (time.perf_counter() - started) * 1000.0
            vad_ms = (time.perf_counter() - self._last_active_wall) * 1000.0
            if topk:
                text = topk[0].text
                self.words.append(text)
                e2e = vad_ms + match_ms + UI_FRAME_MS
                self.state.metrics.record("L1_word", e2e)
                await self.send(
                    GlossEvent(
                        trace_id=self._tid(), t=now_ms(), text=text,
                        score=round(conf, 3), final=True, topk=topk,
                        latency=Latency(
                            vad=int(vad_ms), match=int(match_ms), e2e=int(e2e)
                        ),
                    ).to_json()
                )
                await self.send(VadEvent(self._tid(), now_ms(), "COMMITTING", step.segment_id).to_json())
            else:
                await self._error("no_match", "本段动作没有匹配到词，请重打或从词表点选")
            self.buffer = []
        elif step.action == "sentence_end":
            words, self.words = list(self.words), []
            self.buffer = []
            await self.send(VadEvent(self._tid(), now_ms(), "IDLE", step.segment_id).to_json())
            if words:
                self._spawn(self._translate(words))

    async def _translate(self, words: list[str]) -> None:
        trace = self._tid()

        async def emit(ev: AgentEvent) -> None:
            await self._forward(trace, words, ev)

        try:
            await self.state.orchestrator.translate(
                words, scene_id=self.scene_id, trace_id=trace, emit=emit
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 慢路径失败不能拖垮 WS
            await self._error("agent_failed", f"{type(exc).__name__}: {exc}")

    async def _forward(self, trace: str, words: list[str], ev: AgentEvent) -> None:
        gap = self.vad.config.sentence_gap_ms
        e2e = gap + ev.latency_ms
        if ev.kind == "sentence":
            self.state.metrics.record("L3_fast" if ev.stage == "fast" else "L4_refined", e2e)
            await self.send(
                SentenceEvent(
                    trace_id=trace, t=now_ms(), text=ev.text,
                    source="llm" if ev.stage == "refined" else "fast",
                    stage="refined" if ev.stage == "refined" else "fast",
                    alternative=ev.payload.get("alternative", ""),
                    confidence=float(ev.payload.get("confidence", 0.0) or 0.0),
                    words=words,
                    latency=Latency(compose=int(ev.latency_ms), e2e=int(e2e)),
                ).to_json()
            )
        elif ev.kind == "audio":
            audio = ev.payload.get("audio", b"")
            b64 = base64.b64encode(audio).decode("ascii") if audio else ""
            url = f"data:audio/wav;base64,{b64}" if b64 else ""
            ttfb = int(ev.payload.get("ttfb_ms", 0) or 0)
            self.state.metrics.record("L2_audio", gap + ev.latency_ms)
            await self.send(
                AudioEvent(
                    trace_id=trace, t=now_ms(), text=ev.text,
                    stage="refined" if ev.stage == "refined" else "fast",
                    url=url, dur_ms=0,
                    cache_hit=bool(ev.payload.get("cache_hit", False)),
                    latency=Latency(tts_ttfb=ttfb, e2e=int(gap + ev.latency_ms)),
                ).to_json()
            )
        elif ev.kind == "emergency":
            await self.send(
                ErrorEvent(trace, now_ms(), "emergency", ev.text).to_json()
            )
        elif ev.kind == "error":
            await self.send(
                ErrorEvent(trace, now_ms(), "slow_path_failed", str(ev.payload.get("error", ""))).to_json()
            )

    # ---- 端侧快路径模式：浏览器上送已确认的词，不再上传逐帧骨骼 ----
    async def handle_gloss(self, msg: dict[str, Any]) -> None:
        text = msg["text"]
        self.words.append(text)
        await self.send(json.dumps(
            {"type": "gloss_ack", "text": text, "score": msg.get("score", 0.0),
             "words": self.words, "ts": now_ms()},
            ensure_ascii=False,
        ))

    # ---- 控制消息 ----
    async def handle_control(self, msg: dict[str, Any]) -> None:
        action = msg.get("action")
        if action == "reset":
            self.words.clear()
            self.buffer.clear()
            self.vad.reset()
            self.extractor.reset()
            await self.send(json.dumps({"type": "session", "action": "reset", "ts": now_ms()}, ensure_ascii=False))
        elif action == "sentence_end":
            words, self.words = list(self.words), []
            self.buffer = []
            if words:
                self._spawn(self._translate(words))


# --------------------------------------------------------------------------- 路由


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "uptime_ms": now_ms() - STATE.started_at,
        "templates": STATE.matcher.size,
        "words": len(STATE.matcher.labels),
        "tools": len(STATE.registry.specs()),
    }


@app.get("/api/scenes")
async def scenes() -> list[dict[str, Any]]:
    return SCENES


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    return {
        "latency": STATE.metrics.snapshot(),
        "tools": STATE.registry.stats(),
        "tool_specs": STATE.registry.describe(),
        "matcher": {"templates": STATE.matcher.size, "words": len(STATE.matcher.labels)},
    }


class TemplateRecord(BaseModel):
    label: str = Field(min_length=1, max_length=32)
    speaker: str = ""
    scene_id: str = "hospital"
    frames: list[list[list[float]]]


@app.post("/api/templates/record")
async def record_template(req: TemplateRecord) -> dict[str, Any]:
    if len(req.frames) < 3:
        return {"ok": False, "error": "frames 至少 3 帧"}
    return {"ok": True, **STATE.record_template(req.label, req.frames, req.speaker)}


class WordsRequest(BaseModel):
    words: list[str]
    scene_id: str = "hospital"


@app.post("/api/compose/fast")
async def compose_fast(req: WordsRequest) -> dict[str, Any]:
    started = time.perf_counter()
    text = compose_template(req.words, req.scene_id)
    return {"sentence": text, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2)}


@app.post("/api/translate")
async def translate(req: WordsRequest) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    async def emit(ev: AgentEvent) -> None:
        payload = dict(ev.payload)
        audio = payload.pop("audio", b"")
        if audio:
            payload["audio_bytes"] = len(audio)
        events.append({"kind": ev.kind, "stage": ev.stage, "text": ev.text,
                       "latency_ms": round(ev.latency_ms, 1), "payload": payload})

    result = await STATE.orchestrator.translate(req.words, req.scene_id, emit=emit)
    return {
        "words": result.words,
        "fast_text": result.fast_text,
        "refined_text": result.refined_text,
        "alternative": result.alternative,
        "fast_ms": round(result.fast_ms, 2),
        "cot_ms": round(result.cot_ms, 2),
        "total_ms": round(result.total_ms, 2),
        "degraded": result.degraded,
        "need_confirm": result.need_confirm,
        "emergency": result.emergency,
        "events": events,
    }


@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket, scene: str = "hospital") -> None:
    await ws.accept()
    session = Session(STATE, ws, scene_id=scene)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = parse_client_message(raw)
            except Exception as exc:  # noqa: BLE001
                await session._error("bad_message", str(exc))
                continue
            if msg["type"] == "frame":
                await session.handle_frame(msg)
            elif msg["type"] == "gloss":
                await session.handle_gloss(msg)
            elif msg["type"] == "control":
                await session.handle_control(msg)
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
