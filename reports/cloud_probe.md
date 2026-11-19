# 云端首包探测报告

> 生成时间：2026-11-19 14:31:22
> 机器：Windows-11-10.0.26200-SP0
> 试次：1

## 1. MaaS 结构化 CoT（TTFT）

- 状态：**未执行**（未安装 openai：pip install openai（华为云 MaaS 兼容 OpenAI 协议））
- 配置方式：cp .env.example .env，填 MAAS_API_KEY 后重跑

## 2. SIS 语音合成（首包）

- 状态：**未执行**（未启用，加 --tts）

## 3. 怎么用这份报告

1. 把真实 P95 填回 docs/智能体体系与实时翻译架构.md §13 与 realtime/bench.py 的假设常量；
2. 若 TTFT > 350ms：把 Orchestrator(cot_deadline_ms) 调到 600–900ms，并确认 L4 仍 ≤ 600ms；
3. 若 TTS 首包 > 300ms：词级音频预热从优化项升级为上线必做项（见架构文档 §4.4）。
