"""模型 / 云服务适配层。

原型默认使用 Mock，保证离线可跑、延迟可控；
生产环境只需在 .env 配置好后打开 MaaSProvider / SISProvider（接口不变）。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class LLMCompose:
    sentence: str
    alternative: str = ""
    confidence: float = 0.75
    need_confirm: bool = False
    reasoning: dict[str, Any] = field(default_factory=dict)
    ttfb_ms: float = 0.0
    total_ms: float = 0.0
    provider: str = "mock"


@dataclass(slots=True)
class TTSResult:
    audio: bytes
    format: str = "wav"
    cache_hit: bool = False
    ttfb_ms: float = 0.0
    provider: str = "mock"


class LLMProvider(Protocol):
    async def compose_cot(self, *, words: list[str], scene_id: str, history: list[dict], topk: list[list[str]] | None = None) -> LLMCompose: ...

    async def disambiguate(self, *, topk: list[list[str]], scene_id: str, history: list[dict]) -> dict[str, Any]: ...


class TTSProvider(Protocol):
    async def synthesize(self, *, text: str) -> TTSResult: ...


class MockLLMProvider:
    """确定性的假 LLM：模拟真实 TTFB，并返回"合理但简单"的润色结果。

    参数可调，用于压测与降级演练（比如把 ttfb_ms 调到 3000ms 验证超时兜底）。
    """

    def __init__(self, ttfb_ms: float = 280.0, jitter_ms: float = 60.0, fail_rate: float = 0.0,
                 offline: bool = False) -> None:
        self.ttfb_ms = ttfb_ms
        self.jitter_ms = jitter_ms
        self.fail_rate = fail_rate
        self.offline = offline
        self._rng = random.Random(7)

    async def compose_cot(self, *, words: list[str], scene_id: str, history: list[dict],
                          topk: list[list[str]] | None = None) -> LLMCompose:
        if self.offline:
            raise RuntimeError("LLM offline (演练：云端不可用)")
        wait = max(0.0, self.ttfb_ms + self._rng.uniform(-self.jitter_ms, self.jitter_ms)) / 1000.0
        t0 = time.perf_counter()
        await asyncio.sleep(wait)
        if self._rng.random() < self.fail_rate:
            raise TimeoutError("mock llm timeout")
        sentence, alternative, need_confirm = self._compose(words, scene_id)
        return LLMCompose(
            sentence=sentence,
            alternative=alternative,
            confidence=0.86,
            need_confirm=need_confirm,
            reasoning={
                "intent": _guess_intent(words),
                "kept": list(words),
                "dropped": [],
                "ambiguity": [],
                "reason": "按场景补全虚词并调整语序，未添加新信息",
            },
            ttfb_ms=(time.perf_counter() - t0) * 1000.0,
            total_ms=(time.perf_counter() - t0) * 1000.0,
            provider="mock",
        )

    async def disambiguate(self, *, topk: list[list[str]], scene_id: str, history: list[dict]) -> dict[str, Any]:
        await asyncio.sleep(0.04)
        best = topk[0][0] if topk and topk[0] else ""
        return {"choose": best, "reason": "mock 选择最高分候选"}

    @staticmethod
    def _compose(words: list[str], scene_id: str) -> tuple[str, str, bool]:
        cleaned = [w for w in words if w]
        if not cleaned:
            return "", "", False
        from .tools import compose_template  # 延迟导入：避免 providers <-> tools 循环依赖

        base = compose_template(cleaned, scene_id)
        if not base:
            return "", "", False
        polite = {
            "hospital": "，麻烦您帮我看一下。",
            "bank": "，麻烦帮我办理一下。",
            "government": "，请问需要准备什么材料？",
        }.get(scene_id, "，谢谢。")
        if base.endswith(("？", "?")):
            refined = base
        elif base.endswith("。"):
            refined = base[:-1] + polite
        else:
            refined = base + polite
        alternative = "".join(cleaned) + "。"
        return refined, alternative if alternative != refined else "", False


class MaaSProvider:
    """华为云 MaaS（OpenAI 兼容接口）。未配置密钥时拒绝实例化，避免误用 Mock 上线。"""

    def __init__(self, model: str | None = None) -> None:
        api_key = os.getenv("MAAS_API_KEY", "")
        if not api_key:
            raise RuntimeError("MAAS_API_KEY 未配置；请设置 .env 后再使用 MaaSProvider")
        from openai import AsyncOpenAI  # 延迟导入，离线环境不依赖

        self.client = AsyncOpenAI(
            base_url=os.getenv("MAAS_BASE_URL", "https://api.modelarts-maas.com/v1"),
            api_key=api_key,
        )
        self.model = model or os.getenv("MAAS_LLM_MODEL", "deepseek-v3")

    async def compose_cot(self, *, words: list[str], scene_id: str, history: list[dict],
                          topk: list[list[str]] | None = None) -> LLMCompose:
        from .prompts import build_cot_messages, parse_cot_output

        t0 = time.perf_counter()
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=build_cot_messages(words=words, scene_id=scene_id, history=history, topk=topk),
            temperature=0.1,
            max_tokens=220,
            stream=False,
        )
        text = resp.choices[0].message.content or ""
        parsed = parse_cot_output(text, words)
        return LLMCompose(
            sentence=parsed["sentence"] or "".join(words) + "。",
            alternative=parsed.get("alternative", ""),
            confidence=float(parsed.get("confidence", 0.7)),
            need_confirm=bool(parsed.get("need_confirm", False)),
            reasoning=parsed,
            ttfb_ms=(time.perf_counter() - t0) * 1000.0,
            total_ms=(time.perf_counter() - t0) * 1000.0,
            provider=self.model,
        )


class MockTTSProvider:
    def __init__(self, ttfb_ms: float = 180.0, offline: bool = False) -> None:
        self.ttfb_ms = ttfb_ms
        self.offline = offline
        self._cache: dict[str, bytes] = {}

    async def synthesize(self, *, text: str) -> TTSResult:
        if self.offline:
            raise RuntimeError("TTS offline (演练：云端不可用)")
        if text in self._cache:
            return TTSResult(audio=self._cache[text], cache_hit=True, ttfb_ms=1.0)
        await asyncio.sleep(self.ttfb_ms / 1000.0)
        audio = _silent_wav(duration_ms=min(4000, 220 * max(1, len(text))))
        self._cache[text] = audio
        return TTSResult(audio=audio, ttfb_ms=self.ttfb_ms)


class SISProvider:
    """华为云 SIS 语音合成。未配置 AK/SK 时拒绝实例化。"""

    def __init__(self) -> None:
        if not os.getenv("HW_AK") or not os.getenv("HW_SK"):
            raise RuntimeError("HW_AK/HW_SK 未配置；请设置 .env 后再使用 SISProvider")
        raise NotImplementedError(
            "SIS 接入：使用 huaweicloudsdksis 的 TtsCustomRequest，"
            "返回 16k wav 字节；流式首包延迟目标 <= 300ms。"
        )


def _silent_wav(duration_ms: int, sample_rate: int = 16000) -> bytes:
    """生成占位 wav（静音），让前端音频链路可以真实跑通。上线替换为 SIS 返回音频。"""
    import io
    import wave

    frames = int(sample_rate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def _guess_intent(words: list[str]) -> str:
    joined = "".join(words)
    if any(w in joined for w in ("救命", "报警", "胸痛", "呼吸")):
        return "紧急求助"
    if any(w in joined for w in ("疼", "发烧", "咳嗽", "药", "挂号")):
        return "医疗主诉"
    if any(w in joined for w in ("多少钱", "取钱", "开户")):
        return "银行业务"
    return "日常交流"
