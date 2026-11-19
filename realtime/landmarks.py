"""骨骼归一化与特征构造（快路径第 1 步，零模型）。

输入约定（与 protocol.py 一致）：
    lm:   42 个手部关键点，左手 21 + 右手 21（缺手补 0）
    pose: 可选 33 个姿态点（MediaPipe Pose），11/12 为左右肩

归一化目标：换人 / 换站位 / 换距离时，同一个手语动作的特征尽量一致。
"""

from __future__ import annotations

import numpy as np

HAND_POINTS = 42
HAND_SIZE = 21
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
PALM_INDEX = 9  # MediaPipe Hands：9 为中指根部，用来量手掌尺度


def to_points(raw) -> np.ndarray:
    """把 [[x,y,z], ...] 或 [x,y,z,...] 统一成 (N, 3) 的 float32 数组。"""
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim == 1:
        if arr.size % 3 != 0:
            raise ValueError(f"landmark 扁平数组长度必须是 3 的倍数，收到 {arr.size}")
        arr = arr.reshape(-1, 3)
    elif arr.ndim == 2:
        if arr.shape[1] == 2:
            arr = np.concatenate([arr, np.zeros((arr.shape[0], 1), np.float32)], axis=1)
        elif arr.shape[1] > 3:
            arr = arr[:, :3]
    else:
        raise ValueError(f"不支持的 landmark 形状: {arr.shape}")
    return arr.astype(np.float32, copy=False)


def _norm(v: np.ndarray) -> float:
    return float(np.linalg.norm(v)) + 1e-6


class Normalizer:
    """把人体的手部关键点归一化到"以双肩中点为原点、双肩距离为 1"的坐标系。

    mode:
        auto  —— 有 pose 用 pose 定标，没有就退化成双手腕定标
        pose  —— 只用 pose（缺失则抛错）
        hands —— 只用双手腕定标
        none  —— 原样返回（压测 / 合成数据用）
    """

    def __init__(self, mode: str = "auto") -> None:
        if mode not in {"auto", "pose", "hands", "none"}:
            raise ValueError(f"unknown normalization mode: {mode}")
        self.mode = mode

    def points(self, hand, pose=None) -> np.ndarray:
        """返回 (42, 2) 的全局归一化坐标。"""
        h = to_points(hand)[:, :2]
        if h.shape[0] < HAND_POINTS:
            raise ValueError(f"hand 关键点不足：需要 {HAND_POINTS}，收到 {h.shape[0]}")
        h = h[:HAND_POINTS]
        if self.mode == "none":
            return np.asarray(h, dtype=np.float32).copy()

        pose_pts = None
        if pose is not None and self.mode in {"auto", "pose"}:
            pp = to_points(pose)
            if pp.shape[0] > RIGHT_SHOULDER:
                pose_pts = pp

        if pose_pts is not None:
            center = (pose_pts[LEFT_SHOULDER, :2] + pose_pts[RIGHT_SHOULDER, :2]) / 2.0
            scale = _norm(pose_pts[LEFT_SHOULDER, :2] - pose_pts[RIGHT_SHOULDER, :2])
        elif self.mode == "pose":
            raise ValueError("pose 模式但未收到合法 pose 关键点")
        else:
            wrist_l, wrist_r = h[0], h[HAND_SIZE]
            center = (wrist_l + wrist_r) / 2.0
            scale = _norm(wrist_l - wrist_r)

        return ((h - center) / scale).astype(np.float32)

    def embed(self, normalized: np.ndarray) -> np.ndarray:
        """由归一化坐标构造 168 维帧特征：全局位置(84) + 手部局部位置(84)。

        手部局部坐标以手腕为原点、以手掌大小为尺度，保留手指的精细动作，
        从而让人体尺度、距离变化不影响手型。
        """
        out = np.asarray(normalized, dtype=np.float32)
        local = []
        for offset in (0, HAND_SIZE):
            wrist = out[offset]
            palm = _norm(out[offset + PALM_INDEX] - wrist)
            local.append((out[offset : offset + HAND_SIZE] - wrist) / palm)
        return np.concatenate([out.reshape(-1), np.concatenate(local, axis=0).reshape(-1)])


class FeatureExtractor:
    """在帧特征上追加帧间速度（运动信息），每个手势段开始前必须 reset()。"""

    def __init__(self, velocity_gain: float = 0.5) -> None:
        self.velocity_gain = velocity_gain
        self._prev: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def __call__(self, embed: np.ndarray) -> np.ndarray:
        flat = np.asarray(embed, dtype=np.float32).reshape(-1)
        if self._prev is None:
            vel = np.zeros_like(flat)
        else:
            vel = (flat - self._prev) * self.velocity_gain
        self._prev = flat
        return np.concatenate([flat, vel])


def resample(seq: np.ndarray, length: int) -> np.ndarray:
    """线性插值把时间轴重采样到固定长度（消除快慢差异的第一步）。"""
    seq = np.asarray(seq, dtype=np.float32)
    if seq.ndim != 2:
        raise ValueError("seq 必须是 (T, D)")
    if seq.shape[0] == 0:
        raise ValueError("空序列不能重采样")
    if seq.shape[0] == 1:
        return np.repeat(seq, length, axis=0)
    if seq.shape[0] == length:
        return seq.copy()
    pos = np.linspace(0.0, seq.shape[0] - 1, length)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, seq.shape[0] - 1)
    w = (pos - i0).astype(np.float32)[:, None]
    return (seq[i0] * (1.0 - w) + seq[i1] * w).astype(np.float32)
