/**
 * sign-core 单元测试 + 端侧时延基准。
 * 运行：cd frontend && node --test tests/
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  Normalizer, FeatureExtractor, MotionVAD, DTWMatcher, defaultConfig, resample,
} from "../sign-core.js";

const HANDS = 42;

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function normal(rng, mu, sigma) {
  const u = Math.max(rng(), 1e-9), v = Math.max(rng(), 1e-9);
  return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
function baseHands(rng) {
  // 双手相距约 0.9，保证 wrist 定标稳定
  const left = new Float32Array(HANDS); // 只存 2 维，点 i 在 [i*2, i*2+1]
  const pts = new Float32Array(HANDS * 2);
  for (let i = 0; i < 21; i++) {
    pts[i * 2] = -0.45 + normal(rng, 0, 0.08);
    pts[i * 2 + 1] = 0.10 + normal(rng, 0, 0.08);
    pts[(21 + i) * 2] = 0.45 + normal(rng, 0, 0.08);
    pts[(21 + i) * 2 + 1] = 0.05 + normal(rng, 0, 0.08);
  }
  return pts;
}
function makeWord(rng, base, freq, n, noise, amp, phase) {
  const a = amp === undefined ? 0.12 : amp;
  const ph = phase === undefined ? 0 : phase;
  const frames = [];
  for (let t = 0; t < n; t++) {
    const s = Math.sin((2 * Math.PI * freq * t) / 30 + ph);
    const p = new Float32Array(HANDS * 2);
    for (let i = 0; i < HANDS * 2; i++) {
      p[i] = base[i] + a * s + normal(rng, 0, noise);
    }
    frames.push(p);
  }
  return frames;
}
function idleFrames(rng, base, n) {
  const out = [];
  for (let t = 0; t < n; t++) {
    const p = new Float32Array(HANDS * 2);
    for (let i = 0; i < HANDS * 2; i++) p[i] = base[i] + normal(rng, 0, 0.0002);
    out.push(p);
  }
  return out;
}
function features(points2d, normalizer) {
  const fx = new FeatureExtractor();
  fx.reset();
  return points2d.map((p) => fx.frame(normalizer.embed(p)));
}
function to3d(points2d) {
  const out = new Float32Array(points2d.length / 2 * 3);
  for (let i = 0; i < points2d.length / 2; i++) {
    out[i * 3] = points2d[i * 2];
    out[i * 3 + 1] = points2d[i * 2 + 1];
  }
  return out;
}

test("归一化对平移与尺度不变", () => {
  const rng = mulberry32(7);
  const base = baseHands(rng);
  const frames = makeWord(rng, base, 1.0, 10, 0);
  const norm = new Normalizer("hands");
  const a = frames.map((p) => norm.points(to3d(p)));
  const b = frames.map((p) => {
    const moved = new Float32Array(p.length);
    for (let i = 0; i < p.length; i += 2) {
      moved[i] = p[i] * 1.7 + 0.4;
      moved[i + 1] = p[i + 1] * 1.7 - 0.25;
    }
    return norm.points(to3d(moved));
  });
  for (let f = 0; f < a.length; f++) {
    for (let i = 0; i < a[f].length; i++) {
      assert.ok(Math.abs(a[f][i] - b[f][i]) < 1e-4, "frame " + f + " dim " + i);
    }
  }
});

test("VAD 能切出动作并判定句末", () => {
  const rng = mulberry32(11);
  const base = baseHands(rng);
  const cfg = Object.assign(defaultConfig(), { startEnergy: 0.006, stopEnergy: 0.003 });
  const vad = new MotionVAD(cfg);
  const seq = idleFrames(rng, base, 8).concat(makeWord(rng, base, 1.0, 24, 0.0008), idleFrames(rng, base, 30));
  const actions = [];
  let t = 0;
  for (const frame of seq) {
    t += 1000 / 30;
    const step = vad.step(frame, t);
    if (step.action) actions.push(step.action);
  }
  assert.ok(actions.includes("start"));
  assert.ok(actions.includes("incremental"));
  assert.ok(actions.includes("end"));
  assert.equal(actions[actions.length - 1], "sentence_end");
  assert.equal(vad.state, "IDLE");
});

test("DTW 模板匹配能区分不同频率的动作", () => {
  const rng = mulberry32(23);
  const base = baseHands(rng);
  const norm = new Normalizer("none");
  const specs = [[1.0, 0.0], [1.55, 0.0], [0.65, 0.0]];
  const matcher = new DTWMatcher({ length: 24, coarseK: 3 });
  specs.forEach(([freq], i) => {
    matcher.add("W" + i, features(makeWord(rng, base, freq, 28, 0.0005), norm), "A");
  });
  specs.forEach(([freq], i) => {
    const res = matcher.match(features(makeWord(rng, base, freq, 24, 0.0008), norm));
    assert.ok(res.topk.length > 0);
    assert.equal(res.topk[0].text, "W" + i);
  });
});

test("匹配时延基准（100 条模板，Node 单线程）", () => {
  const rng = mulberry32(2026);
  const base = baseHands(rng);
  const norm = new Normalizer("none");
  const matcher = new DTWMatcher({ length: 24, coarseK: 8 });
  // 每个词一套独立的逐点幅度/相位向量（仿 Python bench），保证 50 条轨迹可区分
  function makeSpec() {
    const amp = new Float32Array(HANDS * 2);
    const phase = new Float32Array(HANDS * 2);
    for (let i = 0; i < HANDS * 2; i++) {
      amp[i] = 0.05 + 0.10 * rng();
      phase[i] = rng() * Math.PI * 2;
    }
    return { freq: 0.5 + 1.2 * rng(), amp, phase };
  }
  function renderSpec(spec, n, noise) {
    const frames = [];
    for (let t = 0; t < n; t++) {
      const p = new Float32Array(HANDS * 2);
      for (let i = 0; i < HANDS * 2; i++) {
        p[i] = base[i] + spec.amp[i] * Math.sin((2 * Math.PI * spec.freq * t) / 30 + spec.phase[i]) + normal(rng, 0, noise);
      }
      frames.push(p);
    }
    return frames;
  }
  const words = [];
  for (let w = 0; w < 50; w++) words.push({ name: "W" + w, spec: makeSpec() });
  for (const word of words) {
    for (let k = 0; k < 2; k++) {
      matcher.add(word.name, features(renderSpec(word.spec, 24 + ((word.name.length + k) % 8), 0.0008), norm), "S" + k);
    }
  }
  const samples = [];
  let ok = 0;
  for (let trial = 0; trial < 100; trial++) {
    const word = words[trial % words.length];
    const frames = renderSpec(word.spec, 24 + (trial % 7), 0.0009);
    const res = matcher.match(features(frames, norm));
    samples.push(res.latencyMs);
    if (res.topk.length && res.topk[0].text === word.name) ok++;
  }
  const sorted = samples.slice().sort((a, b) => a - b);
  const p50 = sorted[Math.floor(sorted.length * 0.5)];
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  console.log("[" + matcher.count + " 模板] match P50=" + p50.toFixed(2) + "ms P95=" + p95.toFixed(2) + "ms 合成Top-1=" + ((ok / samples.length) * 100).toFixed(0) + "%");
  assert.ok(p95 < 30, "匹配 P95 应在 30ms 内（实测 " + p95.toFixed(1) + "ms）");
});

test("resample 处理变长与单帧", () => {
  const frames = [new Float32Array([1, 2, 3]), new Float32Array([4, 5, 6]), new Float32Array([7, 8, 9])];
  const out = resample(frames, 24);
  assert.equal(out.length, 24 * 3);
  const one = resample([new Float32Array([1, 2, 3])], 8);
  assert.equal(one.length, 24);
});
