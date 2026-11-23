"""公网防护：访问口令 + 每 IP 限流（上公网前必开，防止 MaaS/SIS 额度被刷爆）。

两层开关都在 .env（见 .env.example）：
    API_TOKEN               非空即开启口令校验；/api/* 请求必须带 X-API-Token 头
                            （或 ?token= 查询参数，便于浏览器直连调试）。
    RATE_LIMIT_PER_MINUTE   每客户端 IP 每分钟 /api/* 请求上限（滑动窗口），0 = 关闭。

设计要点：
    - /api/health 始终放行（负载均衡/探活需要；它不消耗云额度）；
    - 纯 ASGI 中间件实现，不包装响应体，SSE 流式直出不受影响；
    - 读取 settings 是运行时的，测试可直接改 settings 属性生效；
    - 限流键优先取 Nginx 透传的 X-Real-IP / X-Forwarded-For，直连时退回 socket 对端。
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings

#: 不校验口令、不计限流的路径（探活/文档）
_EXEMPT_PREFIXES = ("/api/health",)


def _client_ip(scope: Scope) -> str:
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    real_ip = headers.get(b"x-real-ip")
    if real_ip:
        return real_ip.decode("latin-1").strip()
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",", 1)[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _provided_token(scope: Scope) -> str:
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    token = headers.get(b"x-api-token")
    if token:
        return token.decode("latin-1").strip()
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    values = query.get("token")
    return values[0].strip() if values else ""


async def _reject(send: Send, status: int, detail: str, extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8")] + (extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _SlidingWindowLimiter:
    """每 key 一分钟滑动窗口计数（近似：两个固定窗口加权，内存恒定）。"""

    def __init__(self) -> None:
        # key -> [当前窗口计数, 当前窗口起点分钟]
        self._hits: dict[str, list] = {}
        self._last_sweep = time.monotonic()

    def allow(self, key: str, limit: int) -> tuple[bool, int]:
        now = time.time()
        window = int(now // 60)
        entry = self._hits.get(key)
        if entry is None or entry[1] != window:
            entry = [0, window]
            self._hits[key] = entry
        entry[0] += 1
        self._maybe_sweep(window)
        if entry[0] > limit:
            retry_after = 60 - int(now % 60) or 60
            return False, retry_after
        return True, 0

    def _maybe_sweep(self, current_window: int) -> None:
        now = time.monotonic()
        if now - self._last_sweep < 30:
            return
        self._last_sweep = now
        stale = [key for key, entry in self._hits.items() if entry[1] < current_window - 1]
        for key in stale:
            del self._hits[key]


class ApiGuardMiddleware:
    """口令校验 + 限流的纯 ASGI 中间件。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._limiter = _SlidingWindowLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if scope.get("method") == "OPTIONS":
            # CORS 预检不带自定义头，直接放行交给 CORSMiddleware
            await self.app(scope, receive, send)
            return
        if not path.startswith("/api/") or path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # ---- 口令 ----
        required = settings.api_token
        if required and _provided_token(scope) != required:
            await _reject(send, 401, "访问口令缺失或错误：请在请求头携带 X-API-Token")
            return

        # ---- 限流 ----
        limit = settings.rate_limit_per_minute
        if limit and limit > 0:
            allowed, retry_after = self._limiter.allow(_client_ip(scope), limit)
            if not allowed:
                await _reject(
                    send,
                    429,
                    f"请求过于频繁（上限 {limit} 次/分钟），请稍后再试",
                    extra_headers=[(b"retry-after", str(retry_after).encode())],
                )
                return

        await self.app(scope, receive, send)
