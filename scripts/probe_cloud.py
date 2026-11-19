"""云端首包探测：MaaS 结构化 CoT 的 TTFT、SIS TTS 首包（W2/W3 用真实值替换 Mock 假设）。

用法（项目根目录，可先 cp .env.example .env 填好密钥）：
    python scripts/probe_cloud.py --runs 5          # 只测 LLM
    python scripts/probe_cloud.py --runs 5 --tts    # 同时尝试 SIS

无密钥时会安全退出并打印配置指引，不会报错。
结果写入 reports/cloud_probe.md，用来替换架构文档里的"假设值"。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "cloud_probe.md"

#: 预算（与 docs 保持一致）
LLM_TTFT_BUDGET_MS = 350.0     # L4：判停后 600ms 内要看到润色句
TTS_TTFB_BUDGET_MS = 300.0     # L2/L5 的关键
LLM_TOTAL_BUDGET_MS = 900.0    # compose.cot 的 deadline


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return float(ordered[idx])


async def probe_llm(runs: int) -> dict:
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return {"ok": False, "reason": "未安装 openai：pip install openai（华为云 MaaS 兼容 OpenAI 协议）"}
    api_key = os.getenv("MAAS_API_KEY", "")
    if not api_key:
        return {"ok": False, "reason": "MAAS_API_KEY 未配置"}

    from agentlayer.prompts import build_cot_messages

    client = AsyncOpenAI(
        base_url=os.getenv("MAAS_BASE_URL", "https://api.modelarts-maas.com/v1"),
        api_key=api_key,
        timeout=20.0,
    )
    model = os.getenv("MAAS_LLM_MODEL", "deepseek-v3")
    messages = build_cot_messages(words=["我", "头疼", "两天"], scene_id="hospital")

    rows: list[dict] = []
    for _ in range(runs):
        started = time.perf_counter()
        ttft = None
        text_parts: list[str] = []
        try:
            stream = await client.chat.completions.create(
                model=model, messages=messages, temperature=0.1, max_tokens=200, stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    if ttft is None:
                        ttft = (time.perf_counter() - started) * 1000.0
                    text_parts.append(delta)
        except Exception as exc:  # noqa: BLE001
            rows.append({"error": f"{type(exc).__name__}: {exc}"})
            continue
        total = (time.perf_counter() - started) * 1000.0
        rows.append({"ttft": ttft or total, "total": total, "chars": len("".join(text_parts))})
    ok_rows = [r for r in rows if "error" not in r]
    if not ok_rows:
        return {"ok": False, "reason": "全部请求失败", "rows": rows}
    ttfts = [r["ttft"] for r in ok_rows]
    totals = [r["total"] for r in ok_rows]
    return {
        "ok": True, "model": model, "runs": len(ok_rows), "errors": len(rows) - len(ok_rows),
        "ttft_p50": pct(ttfts, 50), "ttft_p95": pct(ttfts, 95),
        "total_p50": pct(totals, 50), "total_p95": pct(totals, 95),
        "pass": pct(ttfts, 95) <= LLM_TTFT_BUDGET_MS and pct(totals, 95) <= LLM_TOTAL_BUDGET_MS,
        "samples": [round(t, 1) for t in ttfts],
    }


def probe_tts(runs: int) -> dict:
    """SIS 语音合成首包。SDK 未安装/未配置时返回可诊断原因，不影响 LLM 探测。"""
    try:
        from huaweicloudsdkcore.auth.credentials import BasicCredentials  # noqa: F401
        from huaweicloudsdksis.v1 import SisClient  # noqa: F401
    except ImportError as exc:
        return {"ok": False, "reason": f"未安装 SIS SDK（{exc}）：pip install huaweicloudsdkcore huaweicloudsdksis"}
    if not os.getenv("HW_AK") or not os.getenv("HW_SK"):
        return {"ok": False, "reason": "HW_AK/HW_SK 未配置"}
    # SDK 调用细节随 SIS 版本变化，这里保留明确 TODO 而不是塞一段未验证的代码
    return {
        "ok": False,
        "reason": (
            "SIS SDK 已安装但探测未实现：请按 SIS『语音合成』文档补 TtsCustomRequest，"
            "测量 request 发出到 resp.audio 落盘的毫秒数，目标 P95 <= 300ms（流式首包）"
        ),
    }


def write_report(llm: dict, tts: dict | None, runs: int) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    env_hint = "cp .env.example .env"
    lines = [
        "# 云端首包探测报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 机器：{platform.platform()}",
        f"> 试次：{runs}",
        "",
        "## 1. MaaS 结构化 CoT（TTFT）",
        "",
    ]
    if llm.get("ok"):
        lines += [
            f"- 模型：{llm['model']}",
            f"- TTFT：P50 **{llm['ttft_p50']:.0f}ms** / P95 **{llm['ttft_p95']:.0f}ms**（预算 ≤ {LLM_TTFT_BUDGET_MS:.0f}ms）",
            f"- 总耗时：P50 {llm['total_p50']:.0f}ms / P95 {llm['total_p95']:.0f}ms（deadline {LLM_TOTAL_BUDGET_MS:.0f}ms）",
            f"- 结论：{'PASS' if llm['pass'] else 'FAIL'}",
            f"- 样本 TTFT：{llm['samples']}",
        ]
    else:
        lines += [
            f"- 状态：**未执行**（{llm.get('reason')}）",
            f"- 配置方式：{env_hint}，填 MAAS_API_KEY 后重跑",
        ]
    lines += ["", "## 2. SIS 语音合成（首包）", ""]
    if tts and tts.get("ok"):
        lines += [
            f"- 首包 P50 {tts['ttfb_p50']:.0f}ms / P95 {tts['ttfb_p95']:.0f}ms",
            f"- 结论：{'PASS' if tts['pass'] else 'FAIL'}",
        ]
    else:
        lines += [f"- 状态：**未执行**（{(tts or {}).get('reason', '未启用，加 --tts')}）"]
    lines += [
        "",
        "## 3. 怎么用这份报告",
        "",
        "1. 把真实 P95 填回 docs/智能体体系与实时翻译架构.md §13 与 realtime/bench.py 的假设常量；",
        "2. 若 TTFT > 350ms：把 Orchestrator(cot_deadline_ms) 调到 600–900ms，并确认 L4 仍 ≤ 600ms；",
        "3. 若 TTS 首包 > 300ms：词级音频预热从优化项升级为上线必做项（见架构文档 §4.4）。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> int:
    load_env()
    llm = await probe_llm(args.runs)
    tts = probe_tts(args.runs) if args.tts else None
    write_report(llm, tts, args.runs)
    if llm.get("ok"):
        print("LLM: P95 TTFT %.0fms" % llm["ttft_p95"])
    else:
        print("LLM: 未执行 - " + str(llm.get("reason")))
    print("报告已写入：" + str(REPORT))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--tts", action="store_true", help="同时尝试 SIS TTS 首包探测")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
