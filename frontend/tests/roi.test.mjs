import test from "node:test";
import assert from "node:assert/strict";
import { computeHandBox, computeSegmentRoi } from "../src/utils/roi.js";

test("computeHandBox 返回有效边界且无手时返回 null", () => {
  assert.equal(computeHandBox([]), null);
  assert.equal(computeHandBox(null), null);

  const box = computeHandBox([
    [{ x: 0.3, y: 0.4 }, { x: 0.5, y: 0.6 }],
    [{ x: 0.6, y: 0.35 }, { x: 0.7, y: 0.7 }],
  ]);
  assert.deepEqual(box, { minX: 0.3, minY: 0.35, maxX: 0.7, maxY: 0.7 });
});

test("computeSegmentRoi 无手部框时回退到居中 4:3", () => {
  const roi = computeSegmentRoi([{ handBox: null }, {}], 1280, 720, 4 / 3);
  assert.equal(roi.source, "center-fallback");
  const pixelAspect = (roi.w * 1280) / (roi.h * 720);
  assert.ok(Math.abs(pixelAspect - 4 / 3) < 1e-4, `aspect=${pixelAspect}`);
  assert.ok(Math.abs(roi.w - 0.75) < 1e-4);
  assert.ok(Math.abs(roi.h - 1.0) < 1e-4);
  assert.ok(Math.abs(roi.x - 0.125) < 1e-4);
  assert.equal(roi.y, 0);
});

test("computeSegmentRoi 根据手部联合框扩展头胸上下文并保持 4:3", () => {
  const frames = [
    { handBox: { minX: 0.40, minY: 0.48, maxX: 0.52, maxY: 0.64 } },
    { handBox: { minX: 0.44, minY: 0.45, maxX: 0.58, maxY: 0.68 } },
  ];
  const roi = computeSegmentRoi(frames, 1280, 720, 4 / 3);
  assert.equal(roi.source, "mediapipe-roi");
  const pixelAspect = (roi.w * 1280) / (roi.h * 720);
  assert.ok(Math.abs(pixelAspect - 4 / 3) < 1e-3, `aspect=${pixelAspect}`);
  assert.ok(roi.x >= 0 && roi.y >= 0);
  assert.ok(roi.x + roi.w <= 1 + 1e-9);
  assert.ok(roi.y + roi.h <= 1 + 1e-9);
  // 向上保留了头部上下文
  assert.ok(roi.y < 0.45);
  assert.ok(roi.x <= 0.40 && roi.x + roi.w >= 0.58);
});

test("computeSegmentRoi 边缘手部框严格钳制在画面内", () => {
  const frames = [{ handBox: { minX: 0.85, minY: 0.85, maxX: 0.98, maxY: 0.98 } }];
  const roi = computeSegmentRoi(frames, 1280, 720, 4 / 3);
  assert.equal(roi.source, "mediapipe-roi");
  const pixelAspect = (roi.w * 1280) / (roi.h * 720);
  assert.ok(Math.abs(pixelAspect - 4 / 3) < 1e-3, `aspect=${pixelAspect}`);
  assert.ok(roi.x >= 0 && roi.y >= 0);
  assert.ok(roi.x + roi.w <= 1 + 1e-9);
  assert.ok(roi.y + roi.h <= 1 + 1e-9);
});
