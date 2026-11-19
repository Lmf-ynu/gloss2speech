# 前端（端侧快路径）

## 启动

```bash
# 项目根目录
uvicorn realtime.server:app --host 127.0.0.1 --port 8000
python -m http.server 5173 --directory frontend
# 打开 http://127.0.0.1:5173/camera.html
```

## 组成

| 文件 | 作用 |
|---|---|
| `sign-core.js` | 归一化、特征、VAD、DTW（纯 JS，无依赖；Node 里可直接 import 测试） |
| `sign-worker.js` | Web Worker：逐帧处理、模板录制、IndexedDB 持久化 |
| `camera.html` | MediaPipe 采集 + 骨骼叠加 + 事件渲染 + 延迟 HUD + 评估 CSV 导出 |
| `tests/sign-core.test.mjs` | 5 个单测 + 100 模板匹配基准（P95 < 30ms 断言） |

## 使用流程

1. 填模板名 → 点"录一个词"（空格快捷键）→ 对镜头做**一次**标准动作 → 模板自动保存（IndexedDB）。
2. 每个词录 2 遍，50 词约 20 分钟。
3. 开始打手语：词会先以灰色暂定出现，确认后变实心并上送服务端；停手 0.6s 触发造句与语音。
4. 评估：在"期望词"里填正确词，打完导出 CSV，交给 `scripts/summarize_eval.py` 汇总。
5. "清空模板"会清掉本机 IndexedDB，谨慎操作。

## 与生产版的差异（已标注，别当成已完成）

- 已实现端侧匹配（Node 同构测试通过），但**未在浏览器实机验证**（需联网加载 MediaPipe CDN）。
- 浏览器端尚未做"多模板加权 / 拒识阈值自学习"，这两项在 W1 用真人数据标定。
- 模板同步、账号体系、离线缓存策略尚未实现。
