"""SignBridge 快路径（实时数据面）参考实现。

分层：
    landmarks.py  骨骼归一化 + 特征构造（无模型）
    vad.py        运动能量端点检测（手势切分状态机）
    matcher.py    DTW 模板匹配（零训练，现场录入模板）
    protocol.py   WS 事件协议
    server.py     FastAPI 网关（/ws/stream + REST）
    bench.py      端到端延迟基准（P50/P95 对预算）

设计约束：本层不得调用任何大模型；任何单步 P95 超过 50ms 的实现都要降级或挪到慢路径。
"""
