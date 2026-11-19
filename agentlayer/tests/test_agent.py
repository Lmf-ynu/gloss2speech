"""慢路径智能体：工具契约、降级、先播后修。全部用 asyncio.run 驱动，不依赖 pytest-asyncio。"""

import asyncio

import pytest

from agentlayer.contracts import ToolSpec
from agentlayer.orchestrator import Orchestrator
from agentlayer.providers import LLMCompose, MockLLMProvider, MockTTSProvider
from agentlayer.registry import ToolRegistry
from agentlayer.tools import build_registry, compose_template


def test_tier_budget_is_enforced():
    reg = ToolRegistry()
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name="bad.tool", tier="fast", description="x",
                              fn=lambda: 1, timeout_ms=500))
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name="badname", tier="fast", description="x",
                              fn=lambda: 1, timeout_ms=5))


def test_registry_falls_back_on_timeout():
    reg = ToolRegistry()

    async def slow(**kwargs):
        await asyncio.sleep(0.5)
        return "slow"

    reg.register(ToolSpec(name="demo.slow", tier="slow", description="慢", fn=slow,
                          timeout_ms=200, fallback="demo.fast"))
    reg.register(ToolSpec(name="demo.fast", tier="fast", description="兜底",
                          fn=lambda: "fallback", timeout_ms=5))

    async def run():
        return await reg.call("demo.slow", deadline_ms=80)

    call = asyncio.run(run())
    assert call.ok and call.degraded
    assert call.value == "fallback"


def test_template_compose_rules():
    assert compose_template(["我", "头疼", "两天"], "hospital") == "医生，我头疼两天了。"
    assert compose_template(["挂号"], "hospital") == "你好，我要挂号。"
    assert compose_template([], "hospital") == ""


def test_translate_fast_first_then_refine():
    registry = build_registry(MockLLMProvider(ttfb_ms=80), MockTTSProvider(ttfb_ms=10))
    orch = Orchestrator(registry)
    events = []

    async def emit(ev):
        events.append((ev.kind, ev.stage, ev.text))

    result = asyncio.run(orch.translate(["我", "头疼", "两天"], "hospital", emit=emit))
    assert result.fast_ms < 50, result.fast_ms
    assert events[0][:2] == ("sentence", "fast")
    assert any(kind == "sentence" and stage == "refined" for kind, stage, _ in events)
    assert result.refined_text
    assert not result.degraded
    assert result.total_ms < 600


def test_llm_timeout_degrades_without_raising():
    registry = build_registry(MockLLMProvider(ttfb_ms=5000), MockTTSProvider(ttfb_ms=10))
    orch = Orchestrator(registry, cot_deadline_ms=150)
    result = asyncio.run(orch.translate(["我", "头疼", "两天"], "hospital"))
    assert result.fast_text.startswith("医生")
    assert result.degraded is True
    assert result.total_ms < 900


def test_emergency_short_circuits_models():
    registry = build_registry(MockLLMProvider(ttfb_ms=5000), MockTTSProvider(ttfb_ms=10))
    orch = Orchestrator(registry)
    events = []

    async def emit(ev):
        events.append(ev.kind)

    result = asyncio.run(orch.translate(["我", "救命"], "hospital", emit=emit))
    assert result.emergency is True
    assert "sentence" not in events
    assert result.total_ms < 50


def test_audio_played_once_no_rollback():
    registry = build_registry(MockLLMProvider(ttfb_ms=60), MockTTSProvider(ttfb_ms=5))
    orch = Orchestrator(registry, audio_replace=False)
    events = []

    async def emit(ev):
        events.append(ev)

    result = asyncio.run(orch.translate(["挂号"], "hospital", emit=emit))
    audio_events = [e for e in events if e.kind == "audio"]
    assert result.fast_audio is True
    assert len(audio_events) == 1, [e.stage for e in audio_events]


class _BadProvider(MockLLMProvider):
    """故意返回更差（丢信息）的润色句，验证质量护栏。"""

    async def compose_cot(self, *, words, scene_id, history, topk=None):
        await asyncio.sleep(0.01)
        return LLMCompose(sentence="两天。", alternative="", confidence=0.9, provider="bad")


def test_quality_gate_keeps_fast_sentence():
    registry = build_registry(_BadProvider(), MockTTSProvider(ttfb_ms=5))
    orch = Orchestrator(registry)
    result = asyncio.run(orch.translate(["我", "头疼", "两天"], "hospital"))
    assert result.fast_text == "医生，我头疼两天了。"
    assert result.refined_text == result.fast_text
    assert result.guard_rejected is True


def test_tts_failure_still_returns_sentence():
    registry = build_registry(MockLLMProvider(ttfb_ms=40), MockTTSProvider(offline=True))
    orch = Orchestrator(registry)
    result = asyncio.run(orch.translate(["挂号"], "hospital"))
    assert result.fast_text
    assert result.fast_audio is False
    assert result.total_ms < 800
