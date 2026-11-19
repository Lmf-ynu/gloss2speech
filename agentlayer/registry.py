"""工具注册表 + 截止时间调度器（智能体的"手脚"与"刹车"）。"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from .contracts import ToolCall, ToolSpec


class ToolRegistry:
    def __init__(self, history: int = 500) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._history: list[ToolCall] = []
        self._history_limit = history

    # ---------- 注册 ----------

    def register(self, spec: ToolSpec) -> ToolSpec:
        spec.validate()
        # fallback 允许前向引用（注册顺序无关），调用时才解析
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"未注册的工具：{name!r}") from exc

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def describe(self) -> list[dict[str, Any]]:
        """给模型/前端看的工具清单（就是工具流的 schema）。"""
        return [
            {
                "name": s.name,
                "tier": s.tier,
                "description": s.description,
                "timeout_ms": s.timeout_ms,
                "side_effect": s.side_effect,
                "fallback": s.fallback,
                "input_schema": s.input_schema,
            }
            for s in self._tools.values()
        ]

    # ---------- 调用 ----------

    @property
    def calls(self) -> list[ToolCall]:
        return list(self._history)

    async def call(self, name: str, *, deadline_ms: int | None = None, **kwargs: Any) -> ToolCall:
        return await self._call(name, kwargs, deadline_ms=deadline_ms, depth=0)

    async def _call(self, name: str, kwargs: dict[str, Any], *, deadline_ms: int | None, depth: int) -> ToolCall:
        spec = self.get(name)
        budget = spec.timeout_ms if deadline_ms is None else min(spec.timeout_ms, deadline_ms)
        budget = max(1, int(budget))
        started = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(spec.fn):
                result = await asyncio.wait_for(spec.fn(**kwargs), timeout=budget / 1000.0)
            else:
                # FAST 工具必须是同步纯内存操作，因此直接调用（无事件循环开销）
                result = spec.fn(**kwargs)
            call = ToolCall(name=name, tier=spec.tier, ok=True,
                            latency_ms=(time.perf_counter() - started) * 1000.0,
                            args=kwargs, result=result)
            self._remember(call)
            return call
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            timeout = isinstance(exc, asyncio.TimeoutError)
            call = ToolCall(name=name, tier=spec.tier, ok=False,
                            latency_ms=(time.perf_counter() - started) * 1000.0,
                            args=kwargs, timeout=timeout,
                            error=("timeout" if timeout else f"{type(exc).__name__}: {exc}"))
            self._remember(call)
            if spec.fallback and depth < 2 and spec.fallback in self._tools:
                fb = self.get(spec.fallback)
                # 只把 fallback 需要的参数传过去（保持宽松，便于容错）
                fb_kwargs = {k: v for k, v in kwargs.items() if k in fb.input_schema} or kwargs
                fb_call = await self._call(spec.fallback, fb_kwargs, deadline_ms=deadline_ms, depth=depth + 1)
                fb_call.degraded = True
                fb_call.args = kwargs
                self._remember(fb_call)
                return fb_call
            return call

    def _remember(self, call: ToolCall) -> None:
        self._history.append(call)
        if len(self._history) > self._history_limit:
            del self._history[: len(self._history) - self._history_limit]

    def stats(self) -> dict[str, Any]:
        total = len(self._history)
        fails = sum(1 for c in self._history if not c.ok)
        degraded = sum(1 for c in self._history if c.degraded)
        return {"calls": total, "fails": fails, "degraded": degraded}
