# 摄像头采集自检报告

> 生成时间：2026-11-19 14:50（本机）
> 结论：**摄像头捕获成功**（系统级 + 浏览器 getUserMedia 两级验证通过）。

## 1. 硬件与系统级（OpenCV / DirectShow）

| 项 | 结果 |
|---|---|
| 设备 | USB2.0 HD UVC WebCam（PnP 状态 OK，驱动 usbvideo） |
| 采集后端 | DirectShow（索引 0） |
| 默认分辨率 | 640×480 @ 30.7 fps |
| 目标分辨率 | **1280×720 @ 30.3 fps（YUY2）**，支持 |
| 首帧耗时 | 270ms（含 15 帧预热） |
| 抓帧样例 | 本机截图含人像，**未提交到公开仓库**（已在 .gitignore 排除）；需要时本地跑 `python scripts/camera_check.py` 重新生成 |

## 2. 浏览器级（Chromium headless，getUserMedia）

| 项 | 结果 |
|---|---|
| 页面 | http://127.0.0.1:5173/camera.html |
| getUserMedia | 成功，videoWidth=1280 / videoHeight=720 |
| MediaPipe Tasks Vision | 本地 vendor 加载成功（HandLandmarker + PoseLandmarker，GPU delegate） |
| Web Worker（端侧 VAD+DTW） | 启动正常，模板数 0（未录制） |
| WebSocket | 已连接 ws://127.0.0.1:8000/ws/stream |
| 页面状态 | "已连接，可以开始打手语" |
| JS 报错 | 0 |
| 页面截图 | 同上，本机截图含人像，未入库 |

## 3. 过程中发现并修复的问题

1. **jsdelivr CDN 在本机 Chromium 被 ERR_CONNECTION_RESET**（PowerShell 能访问，浏览器不能）。
   → 已把 MediaPipe JS/WASM/模型全部本地化到 frontend/vendor（约 33MB），演示不再依赖外网。
2. **python -m http.server 不提供 .mjs 的 MIME**（浏览器拒绝加载模块）。
   → vendor bundle 改名为 vision_bundle.js。
3. 首次下载 hand_landmarker.task 被截断（3MB/7.8MB）。
   → fetch_vendor.py 增加 remote Content-Length 校验，可自动补全。

## 4. 待验证 / 注意

- headless 浏览器使用软件 WebGL（SwiftShader），实测仅 3–4 FPS；**真实浏览器有 GPU 时应达到 25–30fps，需人工确认**。
- 若真实浏览器也达不到 15fps：降级顺序为 ① PoseLandmarker 关掉只用双手 ② 摄像头 640×480 ③ MediaPipe 改 CPU delegate 对比。
- 模板数为 0 时识别不会出词，需先按「录一个词」录制。
