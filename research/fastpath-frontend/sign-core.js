/**
 * sign-core.js —— 端侧快路径核心（Zero-training, 纯 JS, 无依赖）
 *
 * 与 Python 参考实现 realtime/ 一一对应：
 *   Normalizer      骨骼归一化（肩中点原点 / 手部局部坐标）
 *   FeatureExtractor 帧特征 = 位置(168) + 速度(168)
 *   MotionVAD       运动能量端点检测（边打边出字 / 判停 / 判句）
 *   DTWMatcher      余弦粗筛 + 带宽 DTW 精排（模板匹配，零训练）
 *
 * 设计约束：全部同步、纯内存；单次匹配目标 < 15ms（Web Worker 中运行，不阻塞 UI）。
 * 在 Node 中可直接 import 做单元测试与基准（见 frontend/tests/sign-core.test.mjs）。
 */

export const HAND_POINTS = 42;
export const HAND_SIZE = 21;
export const PALM_INDEX = 9;
export const D_POINTS = 84;
export const D_EMBED = 168;
export const FEAT_DIM = 336;

export function defaultConfig() {
  return {
    startEnergy: 0.010,
    stopEnergy: 0.005,
    startFrames: 3,
    stopFrames: 5,
    minFrames: 4,
    maxFrames: 150,
    sentenceGapMs: 600,
    incrementalStride: 5,
    frameHz: 30,
  };
}

/** 把 [x,y,z, ...] 或 [[x,y,z], ...] 统一成 Float32Array(n*2)，只取 x/y。 */
export function toPoints2D(raw, n) {
  const out = new Float32Array(n * 2);
  if (!raw) return out;
  if (Array.isArray(raw[0]) || ArrayBuffer.isView(raw[0])) {
    const m = Math.min(n, raw.length);
    for (let i = 0; i < m; i++) {
      out[i * 2] = raw[i][0] || 0;
      out[i * 2 + 1] = raw[i][1] || 0;
    }
    return out;
  }
  const m = Math.min(n, Math.floor(raw.length / 3));
  for (let i = 0; i < m; i++) {
    out[i * 2] = raw[i * 3] || 0;
    out[i * 2 + 1] = raw[i * 3 + 1] || 0;
  }
  return out;
}

export class Normalizer {
  constructor(mode) {
    this.mode = mode || "auto";
    if (["auto", "pose", "hands", "none"].indexOf(this.mode) < 0) {
      throw new Error("unknown normalization mode: " + this.mode);
    }
  }

  /** 返回 (42,2) 的全局归一化坐标（Float32Array(84)）。 */
  points(hand, pose) {
    const h = toPoints2D(hand, HAND_POINTS);
    if (this.mode === "none") return h;

    let cx;
    let cy;
    let scale;
    if (this.mode !== "hands" && pose && pose.length > 12) {
      const p = toPoints2D(pose, 33);
      cx = (p[11 * 2] + p[12 * 2]) / 2;
      cy = (p[11 * 2 + 1] + p[12 * 2 + 1]) / 2;
      scale = Math.hypot(p[11 * 2] - p[12 * 2], p[11 * 2 + 1] - p[12 * 2 + 1]) + 1e-6;
    } else if (this.mode === "pose") {
      throw new Error("pose 模式但未收到合法 pose 关键点");
    } else {
      cx = (h[0] + h[HAND_SIZE * 2]) / 2;
      cy = (h[1] + h[HAND_SIZE * 2 + 1]) / 2;
      scale = Math.hypot(h[0] - h[HAND_SIZE * 2], h[1] - h[HAND_SIZE * 2 + 1]) + 1e-6;
    }

    const out = new Float32Array(D_POINTS);
    for (let i = 0; i < HAND_POINTS; i++) {
      out[i * 2] = (h[i * 2] - cx) / scale;
      out[i * 2 + 1] = (h[i * 2 + 1] - cy) / scale;
    }
    return out;
  }

  /** 由归一化坐标构造 168 维帧特征：全局位置 + 手部局部坐标。 */
  embed(pts) {
    const out = new Float32Array(D_EMBED);
    out.set(pts, 0);
    for (let g = 0; g < 2; g++) {
      const off = g * HAND_SIZE;
      const wx = pts[off * 2];
      const wy = pts[off * 2 + 1];
      const palm = Math.hypot(pts[(off + PALM_INDEX) * 2] - wx, pts[(off + PALM_INDEX) * 2 + 1] - wy) + 1e-6;
      for (let i = 0; i < HAND_SIZE; i++) {
        out[D_POINTS + (off + i) * 2] = (pts[(off + i) * 2] - wx) / palm;
        out[D_POINTS + (off + i) * 2 + 1] = (pts[(off + i) * 2 + 1] - wy) / palm;
      }
    }
    return out;
  }
}

export class FeatureExtractor {
  constructor(velocityGain) {
    this.velocityGain = velocityGain === undefined ? 0.5 : velocityGain;
    this.prev = null;
  }

  reset() {
    this.prev = null;
  }

  frame(embed) {
    const out = new Float32Array(FEAT_DIM);
    out.set(embed, 0);
    if (this.prev) {
      for (let i = 0; i < D_EMBED; i++) {
        out[D_EMBED + i] = (embed[i] - this.prev[i]) * this.velocityGain;
      }
    }
    this.prev = embed.slice();
    return out;
  }
}

/** 线性插值把时间轴重采样到固定长度。frames: Float32Array[] -> Float32Array(length*D)。 */
export function resample(frames, length) {
  const T = frames.length;
  if (T === 0) throw new Error("空序列不能重采样");
  const D = frames[0].length;
  const out = new Float32Array(length * D);
  if (T === 1) {
    for (let i = 0; i < length; i++) out.set(frames[0], i * D);
    return out;
  }
  for (let i = 0; i < length; i++) {
    const pos = (i * (T - 1)) / (length - 1);
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, T - 1);
    const w = pos - i0;
    const a = frames[i0];
    const b = frames[i1];
    const base = i * D;
    for (let d = 0; d < D; d++) {
      out[base + d] = a[d] * (1 - w) + b[d] * w;
    }
  }
  return out;
}

export class MotionVAD {
  constructor(config) {
    this.config = Object.assign(defaultConfig(), config || {});
    this.state = "IDLE";
    this._prev = null;
    this._frames = [];
    this._hot = 0;
    this._cool = 0;
    this._lastActiveT = 0;
    this._segmentId = 0;
  }

  reset() {
    this.state = "IDLE";
    this._prev = null;
    this._frames = [];
    this._hot = 0;
    this._cool = 0;
    this._lastActiveT = 0;
  }

  get segmentId() {
    return this._segmentId;
  }

  step(points, tMs) {
    const cfg = this.config;
    let energy = 0;
    if (this._prev) {
      let sum = 0;
      for (let i = 0; i < points.length / 2; i++) {
        const dx = points[i * 2] - this._prev[i * 2];
        const dy = points[i * 2 + 1] - this._prev[i * 2 + 1];
        sum += Math.hypot(dx, dy);
      }
      energy = sum / (points.length / 2);
    }
    this._prev = points.slice();

    if (this.state === "IDLE") {
      if (energy > cfg.startEnergy) this._hot += 1;
      else this._hot = 0;
      if (this._hot >= cfg.startFrames) {
        this._frames = [points.slice()];
        this.state = "SIGNING";
        this._cool = 0;
        this._hot = 0;
        this._segmentId += 1;
        this._lastActiveT = tMs;
        return { state: this.state, action: "start", energy: energy, segmentId: this._segmentId, segment: null };
      }
      return { state: this.state, action: null, energy: energy, segmentId: this._segmentId, segment: null };
    }

    if (this.state === "SIGNING") {
      this._frames.push(points.slice());
      if (energy > cfg.stopEnergy) {
        this._lastActiveT = tMs;
        this._cool = 0;
      } else {
        this._cool += 1;
      }
      if ((this._cool >= cfg.stopFrames || this._frames.length >= cfg.maxFrames) && this._frames.length >= cfg.minFrames) {
        this.state = "COMMITTING";
        this._cool = 0;
        return { state: this.state, action: "end", energy: energy, segmentId: this._segmentId, segment: this._frames };
      }
      if (this._frames.length % cfg.incrementalStride === 0) {
        return { state: this.state, action: "incremental", energy: energy, segmentId: this._segmentId, segment: this._frames };
      }
      return { state: this.state, action: null, energy: energy, segmentId: this._segmentId, segment: null };
    }

    // COMMITTING：等句子判停；再次运动说明同一句话还没打完
    if (energy > cfg.startEnergy) {
      this._frames = [points.slice()];
      this.state = "SIGNING";
      this._cool = 0;
      this._segmentId += 1;
      this._lastActiveT = tMs;
      return { state: this.state, action: "start", energy: energy, segmentId: this._segmentId, segment: null };
    }
    if (tMs - this._lastActiveT >= cfg.sentenceGapMs) {
      this.state = "IDLE";
      this._frames = [];
      return { state: this.state, action: "sentence_end", energy: energy, segmentId: this._segmentId, segment: null };
    }
    return { state: this.state, action: null, energy: energy, segmentId: this._segmentId, segment: null };
  }

  forceEnd(tMs) {
    if (this.state !== "SIGNING" || this._frames.length < this.config.minFrames) {
      this.state = "IDLE";
      this._frames = [];
      return { state: this.state, action: null, energy: 0, segmentId: this._segmentId, segment: null };
    }
    this.state = "COMMITTING";
    this._cool = 0;
    return { state: this.state, action: "end", energy: 0, segmentId: this._segmentId, segment: this._frames };
  }
}

export class DTWMatcher {
  constructor(opts) {
    const o = opts || {};
    this.length = o.length || 24;
    this.coarseK = Math.max(1, o.coarseK || 8);
    this.bandRatio = o.bandRatio === undefined ? 0.3 : o.bandRatio;
    this.featDim = o.featDim || FEAT_DIM;
    this.labels = [];
    this.speakers = [];
    this.templates = [];   // Float32Array(L*D)
    this.norms = [];
    this._flat = new Float32Array(0);
  }

  get count() {
    return this.labels.length;
  }

  add(label, frames, speaker) {
    if (!frames || frames.length < 2) throw new Error("模板至少需要 2 帧");
    const seq = resample(frames, this.length);
    this.labels.push(label);
    this.speakers.push(speaker || "");
    this.templates.push(seq);
    this._rebuild();
  }

  _rebuild() {
    const n = this.templates.length;
    const dim = this.length * this.featDim;
    this._flat = new Float32Array(n * dim);
    this.norms = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      this._flat.set(this.templates[k], k * dim);
      let s = 0;
      for (let i = 0; i < dim; i++) s += this._flat[k * dim + i] * this._flat[k * dim + i];
      this.norms[k] = Math.sqrt(s) + 1e-6;
    }
  }

  match(frames) {
    const t0 = performance.now();
    if (!this.count) return { topk: [], confidence: 0, distance: Infinity, latencyMs: 0 };

    const L = this.length;
    const D = this.featDim;
    const dim = L * D;
    const q = resample(frames, L);
    let qn = 0;
    for (let i = 0; i < dim; i++) qn += q[i] * q[i];
    qn = Math.sqrt(qn) + 1e-6;

    // 1) 粗筛：余弦相似度
    const cos = new Float32Array(this.count);
    for (let k = 0; k < this.count; k++) {
      const base = k * dim;
      let dot = 0;
      for (let i = 0; i < dim; i++) dot += this._flat[base + i] * q[i];
      cos[k] = dot / (this.norms[k] * qn);
    }
    const order = [];
    for (let k = 0; k < this.count; k++) order.push(k);
    order.sort(function (a, b) { return cos[b] - cos[a]; });
    const cand = order.slice(0, Math.min(this.coarseK, this.count));

    // 2) 精排：带宽 DTW
    const dists = this._dtwBatch(q, cand);
    const best = new Float32Array(this.count);
    best.fill(Infinity);
    for (let i = 0; i < cand.length; i++) best[cand[i]] = dists[i];

    const idx = [];
    for (let k = 0; k < this.count; k++) idx.push(k);
    idx.sort(function (a, b) { return best[a] - best[b]; });

    const byLabel = new Map();
    for (let i = 0; i < idx.length; i++) {
      const k = idx[i];
      if (!isFinite(best[k])) continue;
      const lab = this.labels[k];
      if (!byLabel.has(lab)) byLabel.set(lab, best[k]);
    }
    const ordered = Array.from(byLabel.entries()).sort(function (a, b) { return a[1] - b[1]; });
    const d1 = ordered.length ? ordered[0][1] : Infinity;
    const d2 = ordered.length > 1 ? ordered[1][1] : d1 * 2 + 1e-6;
    const confidence = ordered.length ? Math.max(0, Math.min(1, 1 - d1 / (d2 + 1e-6))) : 0;
    const topk = ordered.slice(0, 3).map(function (entry, i) {
      return { text: entry[0], score: Number((confidence * (1 - 0.05 * i)).toFixed(3)) };
    });
    return { topk: topk, confidence: confidence, distance: d1, latencyMs: performance.now() - t0 };
  }

  _dtwBatch(q, cand) {
    const L = this.length;
    const D = this.featDim;
    const band = Math.max(3, Math.floor(L * this.bandRatio));
    const out = new Float32Array(cand.length);
    for (let c = 0; c < cand.length; c++) {
      const tpl = this.templates[cand[c]];
      const cost = new Float32Array(L * L);
      for (let i = 0; i < L; i++) {
        for (let j = 0; j < L; j++) {
          let s = 0;
          const qb = i * D;
          const tb = j * D;
          for (let d = 0; d < D; d++) {
            const diff = q[qb + d] - tpl[tb + d];
            s += diff * diff;
          }
          cost[i * L + j] = s;
        }
      }
      const dp = new Float32Array(L * L);
      dp.fill(Infinity);
      dp[0] = cost[0];
      for (let j = 1; j < Math.min(L, band + 1); j++) dp[j] = cost[j] + dp[j - 1];
      for (let i = 1; i < L; i++) {
        const lo = Math.max(0, i - band);
        const hi = Math.min(L, i + band + 1);
        for (let j = lo; j < hi; j++) {
          let best;
          if (j === 0) {
            best = dp[(i - 1) * L];
          } else {
            const a = dp[(i - 1) * L + j];
            const b = dp[i * L + j - 1];
            const c2 = dp[(i - 1) * L + j - 1];
            best = a < b ? (a < c2 ? a : c2) : (b < c2 ? b : c2);
          }
          dp[i * L + j] = cost[i * L + j] + best;
        }
      }
      out[c] = Math.sqrt(Math.max(dp[L * L - 1], 0));
    }
    return out;
  }

  /** 导出模板（用于 IndexedDB 持久化）。 */
  exportTemplates() {
    const dim = this.length * this.featDim;
    const out = [];
    for (let k = 0; k < this.count; k++) {
      out.push({
        label: this.labels[k],
        speaker: this.speakers[k],
        seq: this.templates[k].slice(0, dim),
      });
    }
    return out;
  }
}
