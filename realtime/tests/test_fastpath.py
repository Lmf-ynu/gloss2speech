"""快路径（VAD / 归一化 / DTW / 协议）单元测试。"""

import numpy as np
import pytest

from realtime.landmarks import FeatureExtractor, Normalizer, resample
from realtime.matcher import DTWMatcher
from realtime.protocol import parse_client_message
from realtime.vad import MotionVAD, VADConfig


def _word_points(freq: float, phase: float, n: int = 26, noise: float = 0.0, seed: int = 0):
    """合成一段"手在动"的 42 点序列；双手相距约 0.9，保证 wrist 定标稳定。"""
    rng = np.random.default_rng(seed)
    left0 = np.stack([np.full(21, -0.45), np.full(21, 0.10)], axis=1)
    right0 = np.stack([np.full(21, 0.45), np.full(21, 0.05)], axis=1)
    off_l = rng.uniform(-0.15, 0.15, (21, 2))
    off_r = rng.uniform(-0.15, 0.15, (21, 2))
    out = []
    for t in range(n):
        s = np.sin(2 * np.pi * freq * t / 30.0 + phase)
        left = left0 + off_l + 0.10 * s
        right = right0 + off_r + 0.10 * s
        frame = np.concatenate([left, right]).astype(np.float32)
        if noise:
            frame += rng.normal(0, noise, frame.shape).astype(np.float32)
        out.append(frame)
    return np.stack(out)


def _idle(n: int = 8, seed: int = 1):
    rng = np.random.default_rng(seed)
    left0 = np.stack([np.full(21, -0.45), np.full(21, 0.10)], axis=1)
    right0 = np.stack([np.full(21, 0.45), np.full(21, 0.05)], axis=1)
    frame = np.concatenate([left0, right0]).astype(np.float32)
    return np.stack([frame + rng.normal(0, 0.0002, frame.shape) for _ in range(n)])


def _features(frames, normalizer=None):
    normalizer = normalizer or Normalizer("none")
    fx = FeatureExtractor()
    out = []
    for i, f in enumerate(frames):
        if i == 0:
            fx.reset()
        pts = normalizer.points(f)
        out.append(fx(normalizer.embed(pts)))
    return np.stack(out)


def test_normalizer_is_translation_and_scale_invariant():
    frames = _word_points(1.0, 0.3, n=10)
    norm = Normalizer("hands")
    base = np.stack([norm.points(f) for f in frames])
    moved = np.stack([norm.points(f * 1.7 + np.array([0.4, -0.25], dtype=np.float32)) for f in frames])
    np.testing.assert_allclose(base, moved, atol=1e-4)


def test_vad_segments_action_and_emits_sentence_end():
    cfg = VADConfig(start_energy=0.006, stop_energy=0.003, sentence_gap_ms=600)
    vad = MotionVAD(cfg)
    actions = []
    t = 0.0
    for frame in np.concatenate([_idle(8), _word_points(1.0, 0.0, n=24), _idle(30)], axis=0):
        step = vad.step(frame, t)
        if step.action:
            actions.append(step.action)
        t += 1000.0 / 30.0
    assert actions[0] == "start"
    assert "incremental" in actions
    assert "end" in actions
    assert actions[-1] == "sentence_end"
    assert vad.state == "IDLE"


def test_matcher_recognizes_three_words():
    specs = [(1.0, 0.0), (1.55, 0.0), (0.65, 0.0)]   # 不同频率 = 不同轨迹；相位差只是时间平移
    matcher = DTWMatcher(length=24, coarse_k=3)
    for i, (freq, phase) in enumerate(specs):
        matcher.add(f"W{i}", _features(_word_points(freq, phase, n=28, noise=0.0005, seed=i)), speaker="A")
    for i, (freq, phase) in enumerate(specs):
        # 同一签手语者的站位一致（seed 相同），只改变时长与噪声
        query = _features(_word_points(freq, phase, n=24, noise=0.0008, seed=i))
        topk, conf, _ = matcher.match(query)
        assert topk and topk[0].text == f"W{i}", (i, topk)
        assert 0.0 <= conf <= 1.0


def test_resample_handles_variable_length():
    seq = np.random.default_rng(0).normal(size=(37, 8)).astype(np.float32)
    assert resample(seq, 24).shape == (24, 8)
    assert resample(seq[:1], 24).shape == (24, 8)


def test_protocol_parses_nested_and_flat_landmarks():
    msg = parse_client_message('{"type":"frame","lm":[[0.1,0.2,0],[0.3,0.4,0]],"t":10}')
    assert msg["lm"] == [0.1, 0.2, 0.0, 0.3, 0.4, 0.0]
    msg2 = parse_client_message('{"type":"control","action":"reset"}')
    assert msg2["action"] == "reset"
    with pytest.raises(ValueError):
        parse_client_message('{"type":"frame","lm":[]}')
    with pytest.raises(ValueError):
        parse_client_message('{"type":"control","action":"oops"}')
