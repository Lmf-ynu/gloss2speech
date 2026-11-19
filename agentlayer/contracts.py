"""工具契约：智能体只能通过这里声明的工具做事。

设计要点（对应 docs §5.2）：
    1. 每个工具必须声明 tier —— 决定它能占多少时延预算、能否阻塞主链路；
    2. 每个工具必须有 timeout 与 fallback —— 智能体没有"卡住"这个选项；
    3. 副作用显式声明 —— 写库/外部调用必须走慢路径，禁止出现在实时链路里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Tier = Literal["fast", "near", "slow"]
SideEffect = Literal["none", "db_write", "external_call"]

#: 各 tier 的硬预算（ms）。超过即视为实现缺陷。
TIER_BUDGET_MS: dict[str, int] = {"fast": 30, "near": 300, "slow": 3000}


@dataclass(slots=True)
class ToolSpec:
    name: str
    tier: Tier
    description: str
    fn: Callable[..., Any]
    timeout_ms: int = 300
    idempotent: bool = True
    side_effect: SideEffect = "none"
    fallback: str | None = None          # 超时/失败后自动回落的工具名
    emits: tuple[str, ...] = ()          # 会产生的下行事件类型
    input_schema: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.tier not in TIER_BUDGET_MS:
            raise ValueError(f"{self.name}: 未知 tier {self.tier!r}")
        if self.timeout_ms > TIER_BUDGET_MS[self.tier]:
            raise ValueError(
                f"{self.name}: timeout {self.timeout_ms}ms 超出 {self.tier} 层预算 "
                f"{TIER_BUDGET_MS[self.tier]}ms，请降级实现或改层级"
            )
        if not self.name or "." not in self.name:
            raise ValueError(f"工具名必须是 domain.action 形式，收到 {self.name!r}")


@dataclass(slots=True)
class ToolCall:
    name: str
    tier: str
    ok: bool
    latency_ms: float
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    degraded: bool = False      # 是否走了 fallback
    timeout: bool = False

    @property
    def value(self) -> Any:
        return self.result
