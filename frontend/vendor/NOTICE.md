# 第三方资产说明（frontend/vendor）

本目录由 `scripts/fetch_vendor.py` 从官方源下载，用于**离线演示**（现场网络常拦截 CDN），
请勿手工修改。

| 文件 | 来源 | 许可 |
|---|---|---|
| `vision_bundle.js`、`wasm/*` | npm `@mediapipe/tasks-vision@0.10.12` | Apache-2.0（Google MediaPipe） |
| `hand_landmarker.task` | `storage.googleapis.com/mediapipe-models/hand_landmarker/float16/1/` | Apache-2.0 |
| `pose_landmarker_lite.task` | `storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/` | Apache-2.0 |

- 上游项目：https://github.com/google-ai-edge/mediapipe
- 重新下载/校验：`python scripts/fetch_vendor.py`（会对比远端 Content-Length）
- 合计约 33MB：这是"断网也能演示"的代价；如需瘦身，可只保留 `hand_landmarker.task` 并关闭 PoseLandmarker。
