"""编排内核：先播后修（speculative + repair）+ 截止时间调度。

一次翻译的固定流程：
    1) 紧急词规则命中 —— 走旁路，绝不进模型（< 2ms）
    2) 模板速成句  —— 立即出字（< 10ms，L3 的实际值远好于 600ms 预算）
    3) 并行：流式 TTS 先播 + 结构化 CoT 后台润色（各自有 deadline，互不阻塞）
    4) 润色句只更新字幕；已播音频默认不回退（config.audio_replace=False）

对应 docs §5.3 的伪代码。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .providers import LLMCompose
from .registry import ToolRegistry
from .tools import EMERGENCY_TEXT


@dataclass(slots=True)
class Event:
    kind: str
    stage: str = ""
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


@dataclass(slots=True)
class TranslationResult:
    words: list[str] = field(default_factory=list)
    fast_text: str = ""
    refined_text: str = ""
    alternative: str = ""
    confidence: float = 0.0
    fast_ms: float = 0.0
    cot_ms: float = 0.0
    total_ms: float = 0.0
    degraded: bool = False
    need_confirm: bool = False
    emergency: bool = False
    fast_audio: bool = False
    refined_audio: bool = False
    guard_rejected: bool = False
    events: list[Event] = field(default_factory=list)


Emit = Callable[[Event], Any]


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        cot_deadline_ms: int = 900,
        tts_deadline_ms: int = 400,
        audio_replace: bool = False,
    ) -> None:
        self.registry = registry
        self.cot_deadline_ms = cot_deadline_ms
        self.tts_deadline_ms = tts_deadline_ms
        self.audio_replace = audio_replace

    @staticmethod
    def accept_refined(words: list[str], fast: str, refined: str) -> bool:
        """质量护栏：润色结果不允许明显丢信息或显著缩水，否则保留速成句。

        启发式（W3 用回归集 + 人工评审替换）：
            1. 核心词覆盖率 >= 75%（词表内的词必须在结果里出现）；
            2. 长度不低于速成句的 80%。
        """
        if not refined or refined == fast:
            return False
        core = [w for w in words if w]
        if core:
            missing = [w for w in core if w not in refined]
            if len(missing) > max(1, len(core) // 4):
                return False
        if len(refined) < 0.8 * len(fast):
            return False
        return True

    async def _emit(self, emit: Emit | None, event: Event, result: TranslationResult) -> None:
        result.events.append(event)
        if emit is None:
            return
        out = emit(event)
        if inspect.isawaitable(out):
            await out

    async def translate(
        self,
        words: list[str],
        scene_id: str = "hospital",
        trace_id: str = "",
        history: list[dict[str, Any]] | None = None,
        emit: Emit | None = None,
    ) -> TranslationResult:
        t0 = time.perf_counter()
        result = TranslationResult(words=list(words))
        if not words:
            result.total_ms = 0.0
            return result

        # 0) 紧急求助：规则命中直接播预设话术，禁止大模型自由发挥
        emergency = await self.registry.call("emergency.match", words=words)
        if emergency.ok and emergency.value:
            result.emergency = True
            result.fast_text = EMERGENCY_TEXT
            result.total_ms = (time.perf_counter() - t0) * 1000.0
            await self._emit(
                emit,
                Event("emergency", text=EMERGENCY_TEXT,
                      payload={"rule": "EMERGENCY_WORDS"}, latency_ms=result.total_ms),
                result,
            )
            return result

        # 1) 快路径：模板速成句（必须最先出字）
        fast = await self.registry.call("compose.template", words=words, scene_id=scene_id)
        result.fast_text = fast.value if fast.ok else "".join(words) + "。"
        result.fast_ms = (time.perf_counter() - t0) * 1000.0
        await self._emit(
            emit,
            Event("sentence", stage="fast", text=result.fast_text,
                  payload={"source": "template", "degraded": not fast.ok},
                  latency_ms=result.fast_ms),
            result,
        )

        # 2) 并行：先播 + 后台 CoT
        tts_task = asyncio.create_task(
            self.registry.call("tts.stream", text=result.fast_text, deadline_ms=self.tts_deadline_ms)
        )
        cot_task = asyncio.create_task(
            self.registry.call(
                "compose.cot",
                words=words,
                scene_id=scene_id,
                history=history or [],
                deadline_ms=self.cot_deadline_ms,
            )
        )

        tts_call = await tts_task
        if tts_call.ok and isinstance(getattr(tts_call, "value", None), object):
            audio = getattr(tts_call.value, "audio", b"")
            if audio:
                result.fast_audio = True
                await self._emit(
                    emit,
                    Event("audio", stage="fast", text=result.fast_text,
                          payload={
                              "audio": audio,
                              "format": getattr(tts_call.value, "format", "wav"),
                              "cache_hit": getattr(tts_call.value, "cache_hit", False),
                              "ttfb_ms": round(tts_call.latency_ms, 1),
                          },
                          latency_ms=(time.perf_counter() - t0) * 1000.0),
                    result,
                )

        cot_call = await cot_task
        result.cot_ms = (time.perf_counter() - t0) * 1000.0
        if cot_call.ok and isinstance(cot_call.value, LLMCompose):
            refined: LLMCompose = cot_call.value
            result.refined_text = refined.sentence or result.fast_text
            result.alternative = refined.alternative
            result.confidence = float(refined.confidence)
            result.need_confirm = bool(refined.need_confirm)
            result.degraded = bool(cot_call.degraded or refined.provider == "fallback")
            if not self.accept_refined(words, result.fast_text, result.refined_text):
                # 护栏拦截：保留速成句，绝不把更差的结果推给用户
                result.guard_rejected = result.refined_text != result.fast_text
                result.refined_text = result.fast_text
                await self._emit(
                    emit,
                    Event("guard", stage="refined", text="kept_fast",
                          payload={"candidate": refined.sentence},
                          latency_ms=result.cot_ms),
                    result,
                )
            elif result.refined_text and result.refined_text != result.fast_text:
                await self._emit(
                    emit,
                    Event("sentence", stage="refined", text=result.refined_text,
                          payload={
                              "source": refined.provider,
                              "alternative": result.alternative,
                              "confidence": result.confidence,
                              "need_confirm": result.need_confirm,
                              "reasoning": refined.reasoning,
                          },
                          latency_ms=result.cot_ms),
                    result,
                )
            # 快路径音频没播成（TTS 抖动/降级）时，用润色句补一次；已播则不回退
            if not result.fast_audio:
                retry = await self.registry.call(
                    "tts.stream", text=result.refined_text, deadline_ms=self.tts_deadline_ms
                )
                audio = getattr(retry.value, "audio", b"") if retry.ok else b""
                if audio:
                    result.refined_audio = True
                    await self._emit(
                        emit,
                        Event("audio", stage="refined", text=result.refined_text,
                              payload={"audio": audio, "format": "wav"},
                              latency_ms=(time.perf_counter() - t0) * 1000.0),
                        result,
                    )
        else:
            result.degraded = True
            result.refined_text = result.fast_text
            await self._emit(
                emit,
                Event("error", text="slow_path_failed",
                      payload={"tool": cot_call.name, "error": cot_call.error},
                      latency_ms=(time.perf_counter() - t0) * 1000.0),
                result,
            )

        result.total_ms = (time.perf_counter() - t0) * 1000.0
        return result

    async def verify_word(
        self,
        *,
        topk: list[list[str]],
        scene_id: str,
        trace_id: str = "",
        emit: Emit | None = None,
    ) -> str | None:
        """低置信度词的异步复核：只改字幕候选，不动已播音频。"""
        call = await self.registry.call("vision.verify", scene_id=scene_id, topk=topk)
        result = TranslationResult(words=[w[0] for w in topk if w])
        if call.ok and isinstance(call.value, dict) and call.value.get("verified"):
            verified = str(call.value["verified"])
            await self._emit(
                emit,
                Event("correction", text=verified,
                      payload={"source": call.value.get("source", "vlm"),
                               "confidence": call.value.get("confidence", 0.0)},
                      latency_ms=call.latency_ms),
                result,
            )
            return verified
        return None
