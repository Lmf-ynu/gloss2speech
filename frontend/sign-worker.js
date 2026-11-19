/**
 * sign-worker.js —— 端侧快路径 Worker
 *
 * 主线程只负责"摄像头 -> 关键点 -> postMessage"，
 * VAD / 特征 / DTW 匹配全部在本 Worker 中执行（与 UI 渲染解耦，不掉帧）。
 *
 * 模板持久化：IndexedDB（结构化克隆支持 Float32Array，无需编码）。
 * 事件：vad / gloss(provisional|final) / sentence_end / record_done / templates / error
 */
import {
  Normalizer, FeatureExtractor, MotionVAD, DTWMatcher, defaultConfig, FEAT_DIM,
} from "./sign-core.js";

const MATCH_LENGTH = 24;

let matcher = new DTWMatcher({ length: MATCH_LENGTH, coarseK: 8 });
let normalizer = new Normalizer("auto");
let extractor = new FeatureExtractor();
let vad = new MotionVAD(defaultConfig());
let buffer = [];
let recording = null;
let lastActiveAt = 0;
let ready = false;

function post(data) {
  self.postMessage(data);
}

// ---------------- IndexedDB ----------------
const DB_NAME = "signbridge";
const DB_VERSION = 1;
const STORE = "templates";

function openDB() {
  return new Promise(function (resolve, reject) {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = function () {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: "key" });
      }
    };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}

function dbAll() {
  return openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = function () { resolve(req.result || []); };
      req.onerror = function () { reject(req.error); };
    });
  });
}

function dbPut(item) {
  return openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(item);
      tx.oncomplete = function () { resolve(true); };
      tx.onerror = function () { reject(tx.error); };
    });
  });
}

function dbClear() {
  return openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = function () { resolve(true); };
      tx.onerror = function () { reject(tx.error); };
    });
  });
}

function seqToFrames(seq) {
  const frames = [];
  for (let i = 0; i < MATCH_LENGTH; i++) frames.push(seq.slice(i * FEAT_DIM, (i + 1) * FEAT_DIM));
  return frames;
}

async function loadTemplates() {
  try {
    const items = await dbAll();
    for (const item of items) {
      if (!item || !item.seq) continue;
      matcher.add(item.label, seqToFrames(item.seq), item.speaker || "");
    }
    post({ type: "templates", count: matcher.count });
    ready = true;
  } catch (err) {
    post({ type: "error", message: "模板加载失败：" + err.message });
  }
}

// ---------------- 帧处理 ----------------
function handleFrame(msg) {
  const t = typeof msg.t === "number" ? msg.t : performance.now();
  let pts;
  try {
    pts = normalizer.points(msg.lm, msg.pose);
  } catch (err) {
    post({ type: "error", message: String(err && err.message ? err.message : err) });
    return;
  }
  const embed = normalizer.embed(pts);
  const step = vad.step(pts, t);

  if (step.energy > vad.config.startEnergy) lastActiveAt = performance.now();

  if (step.action === "start") {
    extractor.reset();
    buffer = [];
    post({ type: "vad", state: "SIGNING" });
  }
  if (step.state === "SIGNING" || step.state === "COMMITTING") {
    buffer.push(extractor.frame(embed));
  }

  if (step.action === "incremental" && buffer.length >= 6) {
    const res = matcher.match(buffer);
    if (res.topk.length && res.confidence >= 0.30) {
      post({
        type: "gloss", text: res.topk[0].text, score: res.topk[0].score,
        final: false, topk: res.topk, latencyMs: { match: res.latencyMs },
      });
    }
    return;
  }

  if (step.action === "end" && buffer.length >= 4) {
    const res = matcher.match(buffer);
    const vadMs = performance.now() - lastActiveAt;
    const e2eMs = vadMs + res.latencyMs;

    if (recording) {
      saveRecordedTemplate(recording, buffer, vadMs);
      recording = null;
    }

    if (res.topk.length) {
      post({
        type: "gloss", text: res.topk[0].text, score: res.topk[0].score,
        final: true, topk: res.topk,
        latencyMs: { vad: Math.round(vadMs), match: Math.round(res.latencyMs * 10) / 10, e2e: Math.round(e2eMs) },
      });
    } else {
      post({ type: "no_match", segmentFrames: buffer.length });
    }
    buffer = [];
    return;
  }

  if (step.action === "sentence_end") {
    post({ type: "vad", state: "IDLE" });
    post({ type: "sentence_end" });
  }
}

async function saveRecordedTemplate(rec, frames, vadMs) {
  try {
    matcher.add(rec.label, frames, rec.speaker || "web");
    const seq = matcher.exportTemplates();
    const last = seq[seq.length - 1];
    await dbPut({
      key: rec.label + "|" + (rec.speaker || "web") + "|" + Date.now(),
      label: rec.label,
      speaker: rec.speaker || "web",
      seq: last.seq,
      createdAt: Date.now(),
    });
    post({
      type: "record_done", label: rec.label, frames: frames.length,
      templates: matcher.count, vadMs: Math.round(vadMs),
    });
  } catch (err) {
    post({ type: "error", message: "模板保存失败：" + err.message });
  }
}

// ---------------- 消息分发 ----------------
self.onmessage = function (event) {
  const msg = event.data || {};
  switch (msg.type) {
    case "frame":
      handleFrame(msg);
      break;
    case "config":
      if (msg.config) Object.assign(vad.config, msg.config);
      if (msg.mode) normalizer = new Normalizer(msg.mode);
      post({ type: "ready", state: vad.state });
      break;
    case "record_start":
      recording = { label: String(msg.label || "").trim(), speaker: msg.speaker || "web" };
      post({ type: "record_armed", label: recording.label, templates: matcher.count });
      break;
    case "record_cancel":
      recording = null;
      break;
    case "reset":
      vad.reset();
      extractor.reset();
      buffer = [];
      break;
    case "clear_templates":
      matcher = new DTWMatcher({ length: MATCH_LENGTH, coarseK: 8 });
      dbClear().then(function () { post({ type: "templates", count: 0 }); });
      break;
    default:
      post({ type: "error", message: "unknown message: " + msg.type });
  }
};

loadTemplates().then(function () {
  post({ type: "ready", state: vad.state, templates: matcher.count });
});
