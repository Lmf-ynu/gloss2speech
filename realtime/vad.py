"""运动能量端点检测（手势切分状态机）—— 快路径第 2 步。

用"归一化骨骼点的帧间位移"代替像素帧差：
    1. 天然免疫背景、光照、衣物颜色；
    2. 与后续匹配器共用同一套坐标，避免二次变换；
    3. 静止时能量趋近 0，阈值稳定，现场不用反复调。

状态机：
    IDLE      --连续 3 帧 E > start_energy--> SIGNING
    SIGNING   --每 5 帧增量输出 provisional--> 边打边出字
              --连续 5 帧 E < stop_energy--> 输出 end（动作结束）
    COMMITTING--静止 600ms --> 输出 sentence_end（句子结束）
              --再次运动--> 回到 SIGNING（同句继续）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class VADConfig:
    start_energy: float = 0.010
    stop_energy: float = 0.005
    start_frames: int = 3
    stop_frames: int = 5          # 5 帧 @30fps ≈ 167ms 判停去抖
    min_frames: int = 4
    max_frames: int = 150         # 5s 上限，防止一直不停
    sentence_gap_ms: int = 600    # 句末判停
    incremental_stride: int = 5
    frame_hz: float = 30.0


@dataclass(slots=True)
class VADStep:
    state: str = "IDLE"
    action: str | None = None     # start / incremental / end / sentence_end
    energy: float = 0.0
    segment_id: int = 0
    segment: np.ndarray | None = None   # (T, 42, 2)


class MotionVAD:
    def __init__(self, config: VADConfig | None = None) -> None:
        self.config = config or VADConfig()
        self.state = "IDLE"
        self._prev: np.ndarray | None = None
        self._frames: list[np.ndarray] = []
        self._hot = 0
        self._cool = 0
        self._last_active_t = 0
        self._segment_id = 0

    def reset(self) -> None:
        self.state = "IDLE"
        self._prev = None
        self._frames = []
        self._hot = 0
        self._cool = 0
        self._last_active_t = 0

    @property
    def segment_id(self) -> int:
        return self._segment_id

    def _segment(self) -> np.ndarray:
        return np.stack(self._frames)

    def step(self, points: np.ndarray, t_ms: float) -> VADStep:
        points = np.asarray(points, dtype=np.float32)
        if points.ndim != 2:
            raise ValueError("points 必须是 (N, 2)")
        energy = 0.0
        if self._prev is not None and self._prev.shape == points.shape:
            energy = float(np.mean(np.linalg.norm(points - self._prev, axis=1)))
        self._prev = points
        cfg = self.config

        if self.state == "IDLE":
            if energy > cfg.start_energy:
                self._hot += 1
            else:
                self._hot = 0
            if self._hot >= cfg.start_frames:
                self._frames = [points.copy()]
                self.state = "SIGNING"
                self._cool = 0
                self._hot = 0
                self._segment_id += 1
                self._last_active_t = t_ms
                return VADStep(self.state, "start", energy, self._segment_id)
            return VADStep(self.state, None, energy, self._segment_id)

        if self.state == "SIGNING":
            self._frames.append(points.copy())
            if energy > cfg.stop_energy:
                self._last_active_t = t_ms
                self._cool = 0
            else:
                self._cool += 1
            if (self._cool >= cfg.stop_frames or len(self._frames) >= cfg.max_frames) and len(self._frames) >= cfg.min_frames:
                self.state = "COMMITTING"
                self._cool = 0
                return VADStep(self.state, "end", energy, self._segment_id, self._segment())
            if len(self._frames) % cfg.incremental_stride == 0:
                return VADStep(self.state, "incremental", energy, self._segment_id, self._segment())
            return VADStep(self.state, None, energy, self._segment_id)

        # COMMITTING：等句子判停；期间再次运动说明同一句话还没打完
        if energy > cfg.start_energy:
            self._frames = [points.copy()]
            self.state = "SIGNING"
            self._cool = 0
            self._segment_id += 1
            self._last_active_t = t_ms
            return VADStep(self.state, "start", energy, self._segment_id)
        if t_ms - self._last_active_t >= cfg.sentence_gap_ms:
            self.state = "IDLE"
            self._frames = []
            return VADStep(self.state, "sentence_end", energy, self._segment_id)
        return VADStep(self.state, None, energy, self._segment_id)

    def force_end(self, t_ms: float) -> VADStep:
        """UI 按钮 / 空格录制模式的保底出口。"""
        if self.state != "SIGNING" or len(self._frames) < self.config.min_frames:
            self.state = "IDLE"
            self._frames = []
            return VADStep(self.state, None, 0.0, self._segment_id)
        self.state = "COMMITTING"
        self._cool = 0
        seg = self._segment()
        return VADStep(self.state, "end", 0.0, self._segment_id, seg)
