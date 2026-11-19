# gloss2speech · 手语通 SignBridge —— 实时手语翻译（端侧快路径 + 云端智能体）

> 摄像头 30fps → 端侧骨骼关键点 → 运动能量 VAD → 零训练 DTW 词识别 →（只上送词）→ 服务端模板速成句 → 结构化 CoT 润色 → 流式 TTS
>
> GitHub: [xiaoxianasd/gloss2speech](https://github.com/xiaoxianasd/gloss2speech)
>
> **0.5s 结论：L1 停手→词上屏 218ms、L2 停手→词出声 243ms（P95，实测）。整句语音约 0.8s，因为它必须等 600ms 判句——这是指标定义问题，不是性能问题。**

## 文档入口

| 文档 | 作用 |
|---|---|
| [docs/智能体体系与实时翻译架构.md](docs/智能体体系与实时翻译架构.md) | **裁决层**：五指标定义、System1/System2 双平面、工具流、CoT 三档、改造清单、里程碑、ADR |
| [docs/W1验收清单与实测方法.md](docs/W1验收清单与实测方法.md) | **W1 施工单**：50 词 × 3 人 × 10 次测试设计、门禁、CSV 汇总方法 |
| [工程实现方案.md](工程实现方案.md) | 原始工程方案（已加修订横幅，冲突以架构文档为准） |
| [reports/latency_budget.md](reports/latency_budget.md) | 可复现的延迟基准（Python 管线） |
| [reports/cloud_probe.md](reports/cloud_probe.md) | 云端首包探测（有密钥后跑，替换 Mock 假设） |

## 30 秒跑起来

> 前端已把 MediaPipe 的 JS/WASM/模型**本地化到 `frontend/vendor`（约 33MB）**，现场断网也能演示；
> 若 vendor 缺失，执行 `python scripts/fetch_vendor.py` 一键补齐。

```bash
python -m pip install -r requirements.txt

# 1) 后端（慢路径：造句 + TTS + 事件协议）
uvicorn realtime.server:app --host 127.0.0.1 --port 8000

# 2) 前端（端侧 VAD + DTW 在 Web Worker 里跑，需 localhost/HTTPS 才能开摄像头）
python -m http.server 5173 --directory frontend
# 浏览器打开 http://127.0.0.1:5173/camera.html
```

**首次使用必须先录模板（零训练）**：填模板名（如 `头疼`）→ 点"录一个词"（或按空格）→ 对镜头做一次标准动作 → 保存到浏览器 IndexedDB。每个词录 2 遍更稳。

不用摄像头也能验证慢路径：

```bash
curl -s -X POST http://127.0.0.1:8000/api/translate \
  -H "Content-Type: application/json" \
  -d "{\"words\":[\"我\",\"头疼\",\"两天\"],\"scene_id\":\"hospital\"}"
# -> fast_text=医生，我头疼两天了。  fast_ms≈0.06  total_ms≈285（Mock CoT）
```

## 实测延迟（P95）

| 指标 | 含义 | 实测 | 目标 | 来源 |
|---|---|---:|---:|---|
| L1 | 停手 → 词上屏 | **218ms** | 250ms | Python 管线基准 |
| L2 | 停手 → 该词出声 | **243ms** | 450ms | 同上（含音频缓存假设 25ms） |
| L3 | 判停完成 → 速成句 | 17ms | 100ms | 同上 |
| L4 | 判停完成 → CoT 润色句 | 348ms | 600ms | Mock LLM 280ms |
| L5 | 停手 → 整句语音起播 | 797ms | 1000ms | 判句 600 + TTS 180 |
| 端侧匹配 | 100 模板 / 336 维 / 24 帧 | **P50 1.9ms / P95 2.0ms** | 15ms | Node 同构 JS 基准 |

## 架构（一句话版）

```text
浏览器（Web Worker，零训练）
  摄像头 → MediaPipe 关键点 → 归一化 → 运动能量 VAD → DTW 模板匹配 → 词确认
      │  只上送 {"type":"gloss"}（不传帧、不传视频）
      ▼
服务端（FastAPI，慢路径）
  句子缓冲 → 模板速成句（<1ms）→ 并行 { 流式 TTS 先播, 结构化 CoT 后台润色 }
      → 润色句只更新字幕；已播音频不回退；润色更差则质量护栏保留速成句
```

## 目录结构

```text
frontend/                 端侧快路径（System 1）
  sign-core.js            归一化 / 特征 / VAD / DTW（纯 JS，Node 可测）
  sign-worker.js          Web Worker 调度 + IndexedDB 模板持久化
  camera.html             采集 + 叠加渲染 + 端侧事件 + 延迟 HUD + 评估 CSV
  tests/sign-core.test.mjs Node 单测 + 匹配时延基准
realtime/                 Python 参考实现（服务端 VAD/DTW，用于压测与回归）
  landmarks.py vad.py matcher.py protocol.py server.py bench.py
agentlayer/               慢路径智能体（System 2）
  contracts.py registry.py tools.py providers.py prompts.py orchestrator.py
frontend/vendor/          MediaPipe JS/WASM + 两个模型（本地化，离线可跑，约 33MB）
scripts/                  camera_check.py / browser_check.py / fetch_vendor.py
                          probe_cloud.py（云端首包） / summarize_eval.py（W1 汇总） / e2e_ws_check.py（WS 冒烟）
docs/                     架构裁决 + W1 验收清单
reports/                  基准与探测报告
```

## 摄像头自检（本轮已验证）

```bash
# 1) 系统级：枚举设备 + 抓帧 + 测分辨率/帧率（OpenCV / DirectShow）
python scripts/camera_check.py
python scripts/camera_res_check.py

# 2) 浏览器级：无头 Chromium 真实打开页面，检查 getUserMedia / MediaPipe / Worker / WS
python -m pip install playwright && python -m playwright install chromium
python scripts/browser_check.py
```

本机实测结论（详见 [reports/camera_capture_check.md](reports/camera_capture_check.md)）：
USB2.0 HD UVC WebCam 支持 1280×720@30fps；浏览器 getUserMedia 成功拿到 1280×720，
MediaPipe 本地加载 + Worker + WS 全部正常，JS 零报错。

## 测试与基准

```bash
python -m pytest -q                              # 23 passed（Python）
node --test frontend/tests/sign-core.test.mjs    # 5 passed（端侧核心，含 P95 断言）
python realtime/bench.py --words 50 --trials 200 # 重新生成 reports/latency_budget.md
python scripts/probe_cloud.py --runs 5           # 有 MAAS_API_KEY 后测真实 TTFT
```

## 接真实华为云

1. ```cp .env.example .env```，填 MaaS / SIS 密钥；
2. `agentlayer/providers.py`：`MockLLMProvider → MaaSProvider`，`MockTTSProvider → SISProvider`；
3. 视觉大模型只在 `vision.verify`（异步复核）与教学点评里出现，**不回热路径**；
4. 上线必须 HTTPS/WSS（`getUserMedia` 只在安全上下文可用），密钥只放后端。

## 已知限制（不要假装它们不存在）

1. **真人识别准确率未验证**：DTW 在合成数据 100%，跨人泛化是最大风险；W1 验收（50 词 × 3 人 × 10 次）是上线门禁。
2. 前端已在**无头 Chromium + 真实摄像头**下验证 getUserMedia/MediaPipe/Worker/WS 全部正常（见 reports/camera_capture_check.md）；
   但**尚未在真实浏览器（GPU 加速）中人工确认 FPS 与手势识别效果**——headless 软件渲染只有 3–4fps，不代表真实性能。
3. 云端时延仍是 Mock/假设值（LLM TTFB 280ms、TTS 首包 180ms），需 W2–W3 用 `probe_cloud.py` + 真实 SIS 压测替换。
4. 封闭词表识别，不是开放词汇连续手语翻译。
5. 模板存在浏览器 IndexedDB，换设备需重录（后续做云端模板同步）。

## 下一步

- **W1**：真人模板 + 评估，过 G1–G7 门禁（见 W1 文档）
- **W2**：SIS 流式 TTS 接入 + 词级音频预热，压 L2/L5
- **W3**：MaaS 真实 CoT（结构化输出 + 超时降级演练），编造率 0
- **W4**：实时 ASR 反向通道 + Navigator 任务流 + ECS 部署（HTTPS/WSS）
