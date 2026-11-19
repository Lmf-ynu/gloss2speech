"""WS 事件协议 v0.1 —— 快路径 / 慢路径向前端推送的唯一出口。

三条硬规则
1. 事件不可变、可重放：每个事件都带 trace_id + 相对时间戳（服务端单调时钟，ms）；
2. 所有延迟字段都由服务端测量，前端只展示、不计算，避免"客户端时钟不可信"；
3. 任何可修正的结果都必须带 final 语义：前端先渲染 provisional，后到的 final 覆盖它，
   已播放的音频不回退（只修正字幕），见 docs/智能体体系与实时翻译架构.md §5.4。

方向约定
  浏览器 -> 服务端：frame / control
  服务端 -> 浏览器：vad / gloss / sentence / audio / metrics / error
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

PROTOCOL_VERSION = "0.1"

EventType = Literal["vad", "gloss", "sentence", "audio", "metrics", "error", "session"]


def now_ms() -> int:
    """服务端单调时钟（ms）。"""
    return int(time.monotonic() * 1000)


def _latency_dict(latency) -> dict[str, int]:
    """asdict 会把嵌套 dataclass 递归成 dict，这里统一两种形态。"""
    if isinstance(latency, dict):
        return {k: v for k, v in latency.items() if v is not None}
    return latency.as_dict()


@dataclass(slots=True)
class Latency:
    """分阶段延迟（ms）。字段缺省表示该阶段未发生。"""

    capture: int | None = None      # 端侧：摄像头帧 -> 关键点
    transport: int | None = None    # 上行：关键点 -> 服务端
    vad: int | None = None          # 动作结束判定（含 stop 去抖）
    match: int | None = None        # 词匹配
    compose: int | None = None      # 词序列 -> 译文首字
    tts_ttfb: int | None = None     # 译文 -> 音频首包
    e2e: int | None = None          # 从用户最后一个动作结束到当前事件可见

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(slots=True)
class Token:
    text: str
    score: float
    gloss_id: str = ""


@dataclass(slots=True)
class GlossEvent:
    trace_id: str
    t: int
    text: str
    score: float
    final: bool                      # False = 增量/暂定，True = 该动作已确认
    topk: list[Token] = field(default_factory=list)
    latency: Latency = field(default_factory=Latency)
    type: EventType = "gloss"

    def to_json(self) -> str:
        d = asdict(self)
        d["latency_ms"] = _latency_dict(d.pop("latency"))
        d["ts"] = d.pop("t")
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class SentenceEvent:
    trace_id: str
    t: int
    text: str
    source: Literal["fast", "llm", "cot"] = "fast"
    stage: Literal["fast", "refined"] = "fast"
    alternative: str = ""
    confidence: float = 0.0
    words: list[str] = field(default_factory=list)
    latency: Latency = field(default_factory=Latency)
    type: EventType = "sentence"

    def to_json(self) -> str:
        d = asdict(self)
        d["latency_ms"] = _latency_dict(d.pop("latency"))
        d["ts"] = d.pop("t")
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class AudioEvent:
    trace_id: str
    t: int
    text: str
    stage: Literal["fast", "refined"] = "fast"
    url: str = ""                    # 生产环境走 OBS/CDN；原型用 data URL
    dur_ms: int = 0
    cache_hit: bool = False
    latency: Latency = field(default_factory=Latency)
    type: EventType = "audio"

    def to_json(self) -> str:
        d = asdict(self)
        d["latency_ms"] = _latency_dict(d.pop("latency"))
        d["ts"] = d.pop("t")
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class VadEvent:
    trace_id: str
    t: int
    state: Literal["IDLE", "SIGNING", "COMMITTING"]
    segment_id: int = 0
    type: EventType = "vad"

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = d.pop("t")
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class ErrorEvent:
    trace_id: str
    t: int
    code: str
    message: str
    type: EventType = "error"

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = d.pop("t")
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


# ---------- 客户端上行消息 ----------

def parse_client_message(raw: str) -> dict[str, Any]:
    """解析并校验浏览器上行 JSON。失败抛 ValueError（由 server 转成 error 事件）。"""
    msg = json.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("message must be an object")
    kind = msg.get("type")
    if kind == "frame":
        lm = msg.get("lm")
        if not isinstance(lm, list) or not lm:
            raise ValueError("frame.lm must be a non-empty list")
        # 展平兼容：[[x,y,z],...] 或 [x,y,z,...]
        if isinstance(lm[0], list):
            msg["lm"] = [float(v) for p in lm for v in p[:3]]
        else:
            msg["lm"] = [float(v) for v in lm]
        return msg
    if kind == "gloss":
        # 端侧快路径模式：浏览器自己完成 VAD+DTW，只把确认词上送（不传帧）
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("gloss.text 必须是非空字符串")
        msg["text"] = text.strip()[:32]
        try:
            msg["score"] = float(msg.get("score", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("gloss.score 必须是数字") from exc
        return msg
    if kind == "control":
        if msg.get("action") not in {"record_template", "clear_templates", "reset", "sentence_end"}:
            raise ValueError(f"unknown control action: {msg.get('action')!r}")
        return msg
    raise ValueError(f"unknown message type: {kind!r}")
