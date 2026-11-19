/**
 * 帧差法动作检测（对应《工程实现方案》§3.1）
 *
 * 规则：
 *   - 每 100ms 从 <video> 取一帧，缩成 64×48 灰度图
 *   - 计算与上一帧的平均像素差 diff
 *   - IDLE：连续 3 帧 diff > T_start → SIGNING，开始缓存帧
 *   - SIGNING：缓存原始帧（每 200ms 一张，最多 30 张）；
 *              连续 5 帧 diff < T_stop → 动作结束，回调 onActionEnd(frames)
 *   - 动作结束后静止 1.5s → 句子结束，回调 onSentenceEnd()
 */
export class MotionDetector {
  constructor(options) {
    const opts = options || {};
    this.config = {
      sampleInterval: 100,
      tStart: opts.tStart != null ? opts.tStart : 10,
      tStop: opts.tStop != null ? opts.tStop : 6,
      startFrames: 3,
      stopFrames: 5,
      sentenceSilence: 1500,
      cacheInterval: 200,
      maxFrames: 30,
    };
    this.state = "IDLE";
    this.diff = 0;
    this.frames = [];

    this._hot = 0;
    this._cold = 0;
    this._prevGray = null;
    this._lastSampleAt = 0;
    this._lastCacheAt = 0;
    this._lastMotionAt = 0;

    this._sampleCanvas = document.createElement("canvas");
    this._sampleCanvas.width = 64;
    this._sampleCanvas.height = 48;
    this._sampleCtx = this._sampleCanvas.getContext("2d", { willReadFrequently: true });

    this.onActionEnd = null;
    this.onSentenceEnd = null;
    this.onStateChange = null;
  }

  setThresholds(tStart, tStop) {
    if (tStart != null) this.config.tStart = tStart;
    if (tStop != null) this.config.tStop = tStop;
  }

  reset() {
    this.state = "IDLE";
    this.diff = 0;
    this.frames = [];
    this._hot = 0;
    this._cold = 0;
    this._prevGray = null;
    this._lastSampleAt = 0;
    this._lastCacheAt = 0;
    this._lastMotionAt = 0;
    if (this.onStateChange) this.onStateChange("IDLE");
  }

  _setState(next) {
    if (this.state === next) return;
    this.state = next;
    if (this.onStateChange) this.onStateChange(next);
  }

  _cacheFrame(video, now) {
    if (this.frames.length >= this.config.maxFrames) return;
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d").drawImage(video, 0, 0, width, height);
    this.frames.push({ canvas: canvas, at: now });
  }

  /** 每帧调用（放在 requestAnimationFrame 里）；内部每 100ms 采样一次。 */
  push(video, now) {
    if (!video || video.readyState < 2 || !video.videoWidth) return this.state;
    if (now - this._lastSampleAt < this.config.sampleInterval) return this.state;
    this._lastSampleAt = now;

    this._sampleCtx.drawImage(video, 0, 0, 64, 48);
    const data = this._sampleCtx.getImageData(0, 0, 64, 48).data;
    const gray = new Float32Array(64 * 48);
    for (let i = 0; i < gray.length; i += 1) {
      const p = i * 4;
      gray[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
    }

    let diff = 0;
    if (this._prevGray) {
      let sum = 0;
      for (let i = 0; i < gray.length; i += 1) {
        sum += Math.abs(gray[i] - this._prevGray[i]);
      }
      diff = sum / gray.length;
    }
    this._prevGray = gray;
    this.diff = diff;

    const cfg = this.config;

    if (this.state === "IDLE") {
      if (diff > cfg.tStart) this._hot += 1;
      else this._hot = 0;
      if (this._hot >= cfg.startFrames) {
        this._hot = 0;
        this._cold = 0;
        this.frames = [];
        this._lastCacheAt = 0;
        this._lastMotionAt = now;
        this._cacheFrame(video, now);
        this._setState("SIGNING");
      }
      return this.state;
    }

    if (this.state === "SIGNING") {
      if (diff > cfg.tStop) {
        this._lastMotionAt = now;
        this._cold = 0;
      } else {
        this._cold += 1;
      }
      if (now - this._lastCacheAt >= cfg.cacheInterval) {
        this._lastCacheAt = now;
        this._cacheFrame(video, now);
      }
      if (this._cold >= cfg.stopFrames) {
        this._cold = 0;
        this._lastMotionAt = now;
        this._setState("COMMITTING");
        const frames = this.frames.slice();
        if (this.onActionEnd) this.onActionEnd(frames);
      }
      return this.state;
    }

    // COMMITTING：等句子结束（1.5s 静止）；期间再次运动说明同句还有下一个词
    if (diff > cfg.tStart) {
      this._hot += 1;
      if (this._hot >= cfg.startFrames) {
        this._hot = 0;
        this._cold = 0;
        this.frames = [];
        this._lastCacheAt = 0;
        this._lastMotionAt = now;
        this._cacheFrame(video, now);
        this._setState("SIGNING");
      }
    } else {
      this._hot = 0;
    }
    if (now - this._lastMotionAt >= cfg.sentenceSilence) {
      this._cold = 0;
      this._setState("IDLE");
      this.frames = [];
      if (this.onSentenceEnd) this.onSentenceEnd();
    }
    return this.state;
  }
}
