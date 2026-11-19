"""零训练手语词匹配：DTW 模板匹配（快路径第 3 步）。

为什么不训练模型：
    - 现场进入场景后，签手语者按标准动作每个词录 2~3 遍即可用，10 分钟完成 50 词；
    - 换词、加词不需要重训；
    - 可解释：能画出 query 与模板的时间对齐路径，答辩时直接可视化。

两级匹配（保证 P95 < 15ms）：
    1) 粗筛：时间重采样到固定长度后，用余弦距离一次矩阵乘法扫全部模板（~0.5ms）；
    2) 精排：对 Top-K 做带带宽约束的 DTW（~2ms），消除打快打慢的差异。
"""

from __future__ import annotations

import time

import numpy as np

from .landmarks import resample
from .protocol import Token


class DTWMatcher:
    def __init__(
        self,
        length: int = 24,
        coarse_k: int = 8,
        band_ratio: float = 0.3,
        reject_distance: float | None = None,
    ) -> None:
        self.length = length
        self.coarse_k = max(1, coarse_k)
        self.band_ratio = band_ratio
        self.reject_distance = reject_distance
        self._labels: list[str] = []
        self._speakers: list[str] = []
        self._seqs: list[np.ndarray] = []
        self._flat = np.zeros((0, 1), dtype=np.float32)
        self._templates = np.zeros((0, 1, 1), dtype=np.float32)
        self._norms = np.zeros((0,), dtype=np.float32)

    # ---------- 模板管理 ----------

    @property
    def size(self) -> int:
        return len(self._labels)

    @property
    def labels(self) -> list[str]:
        return sorted(set(self._labels))

    def clear(self) -> None:
        self._labels.clear()
        self._speakers.clear()
        self._seqs.clear()
        self._flat = np.zeros((0, 1), dtype=np.float32)
        self._templates = np.zeros((0, 1, 1), dtype=np.float32)
        self._norms = np.zeros((0,), dtype=np.float32)

    def add(self, label: str, sequence: np.ndarray, speaker: str = "") -> None:
        seq = np.asarray(sequence, dtype=np.float32)
        if seq.ndim != 2 or seq.shape[0] < 2:
            raise ValueError("模板必须是 (T>=2, D) 的特征序列")
        self._labels.append(label)
        self._speakers.append(speaker)
        self._seqs.append(resample(seq, self.length))
        self._rebuild()

    def export(self) -> list[tuple[str, str, np.ndarray]]:
        """导出模板（已重采样到统一长度），用于持久化到磁盘。"""
        return [(label, speaker, seq.copy())
                for label, speaker, seq in zip(self._labels, self._speakers, self._seqs)]

    def _rebuild(self) -> None:
        if not self._seqs:
            self._flat = np.zeros((0, 1), dtype=np.float32)
            self._templates = np.zeros((0, 1, 1), dtype=np.float32)
            self._norms = np.zeros((0,), dtype=np.float32)
            return
        arr = np.stack(self._seqs).astype(np.float32)     # (N, L, D)
        self._templates = arr
        self._flat = arr.reshape(arr.shape[0], -1)
        self._norms = np.linalg.norm(self._flat, axis=1) + 1e-6

    # ---------- 匹配 ----------

    def match(self, sequence: np.ndarray) -> tuple[list[Token], float, float]:
        """返回 (topk, confidence, best_distance)。topk 已按分数降序。"""
        started = time.perf_counter()
        if not self._seqs:
            return [], 0.0, float("inf")
        query = resample(np.asarray(sequence, dtype=np.float32), self.length)
        qf = query.reshape(-1)
        qn = float(np.linalg.norm(qf)) + 1e-6

        # 1) 粗筛：余弦距离，一次 matmul
        cos = self._flat @ qf / (self._norms * qn)
        coarse = 1.0 - cos
        k = min(self.coarse_k, len(coarse))
        cand = np.argsort(coarse)[:k]

        # 2) 精排：带带宽约束的批量 DTW
        dists = self._dtw_batch(query, cand)
        best = np.full(len(coarse), np.inf, dtype=np.float64)
        best[cand] = dists

        # 同 label 取最小距离去重
        by_label: dict[str, float] = {}
        for idx in np.argsort(best):
            if not np.isfinite(best[idx]):
                continue
            label = self._labels[idx]
            if label not in by_label:
                by_label[label] = float(best[idx])

        ordered = sorted(by_label.items(), key=lambda kv: kv[1])
        d1 = ordered[0][1] if ordered else float("inf")
        d2 = ordered[1][1] if len(ordered) > 1 else d1 * 2.0 + 1e-6

        if self.reject_distance is not None and d1 > self.reject_distance:
            return [], 0.0, d1

        conf = max(0.0, min(1.0, 1.0 - d1 / (d2 + 1e-6)))
        topk = [Token(text=label, score=round(float(conf) * (1.0 - 0.05 * i), 3)) for i, (label, _) in enumerate(ordered[:3])]
        elapsed = (time.perf_counter() - started) * 1000.0
        return topk, conf, d1 if np.isfinite(d1) else 0.0

    def _dtw_batch(self, query: np.ndarray, cand: np.ndarray) -> np.ndarray:
        L = self.length
        K = len(cand)
        templates = self._templates[cand]                   # (K, L, D)
        # cost[i, j, k] = ||query_i - template_kj||^2
        q2 = np.einsum("ij,ij->i", query, query)            # (L,)
        t2 = np.einsum("kij,kij->ki", templates, templates)  # (K, L)
        dot = np.tensordot(query, templates, axes=([1], [2]))  # (L, K, L)
        cost = q2[:, None, None] - 2.0 * dot + t2[None, :, :]

        band = max(3, int(L * self.band_ratio))
        inf = float("inf")
        dp = np.full((L, L, K), inf, dtype=np.float64)   # dp[i, j, k]
        dp[0, 0] = cost[0, :, 0]
        for j in range(1, min(L, band + 1)):
            dp[0, j] = cost[0, :, j] + dp[0, j - 1]
        for i in range(1, L):
            lo = max(0, i - band)
            hi = min(L, i + band + 1)
            for j in range(lo, hi):
                c = cost[i, :, j]
                if j == 0:
                    best = dp[i - 1, 0]
                else:
                    best = np.minimum(np.minimum(dp[i - 1, j], dp[i, j - 1]), dp[i - 1, j - 1])
                dp[i, j] = c + best
        return np.sqrt(np.maximum(dp[L - 1, L - 1], 0.0))
