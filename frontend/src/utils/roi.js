/**
 * ROI 裁切计算（纯函数，无 DOM 依赖）：
 *   - 根据 MediaPipe 21 点手部关键点计算归一化外接框
 *   - 汇总整段动作所有帧的手部联合框，向头部/胸前适度扩展上下文并保持 4:3 无拉伸裁切
 */

export function computeHandBox(landmarksList) {
  if (!Array.isArray(landmarksList) || landmarksList.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let count = 0;

  for (const hand of landmarksList) {
    if (!Array.isArray(hand)) continue;
    for (const pt of hand) {
      if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.y)) continue;
      const x = Math.max(0, Math.min(1, pt.x));
      const y = Math.max(0, Math.min(1, pt.y));
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      count += 1;
    }
  }

  if (count === 0) return null;
  return { minX, minY, maxX, maxY };
}

function fitTargetAspect(cx, cy, rawW, rawH, normAspect) {
  let w = Math.max(0.05, rawW);
  let h = Math.max(0.05, rawH);

  if (w / h < normAspect) {
    w = h * normAspect;
  } else {
    h = w / normAspect;
  }

  if (w > 1) {
    w = 1;
    h = w / normAspect;
  }
  if (h > 1) {
    h = 1;
    w = h * normAspect;
  }

  w = Math.min(1, Math.max(0.05, w));
  h = Math.min(1, Math.max(0.05, h));

  const x = Math.max(0, Math.min(1 - w, cx - w / 2));
  const y = Math.max(0, Math.min(1 - h, cy - h / 2));
  return { x, y, w, h };
}

export function computeSegmentRoi(frames, videoWidth = 1280, videoHeight = 720, targetAspect = 4 / 3) {
  const safeW = videoWidth > 0 ? videoWidth : 1280;
  const safeH = videoHeight > 0 ? videoHeight : 720;
  const normAspect = (targetAspect > 0 ? targetAspect : 4 / 3) * (safeH / safeW);

  let uMinX = Infinity;
  let uMinY = Infinity;
  let uMaxX = -Infinity;
  let uMaxY = -Infinity;
  let hasBox = false;

  if (Array.isArray(frames)) {
    for (const item of frames) {
      const box = item && item.handBox;
      if (!box) continue;
      if (
        !Number.isFinite(box.minX) ||
        !Number.isFinite(box.minY) ||
        !Number.isFinite(box.maxX) ||
        !Number.isFinite(box.maxY)
      ) {
        continue;
      }
      hasBox = true;
      if (box.minX < uMinX) uMinX = box.minX;
      if (box.minY < uMinY) uMinY = box.minY;
      if (box.maxX > uMaxX) uMaxX = box.maxX;
      if (box.maxY > uMaxY) uMaxY = box.maxY;
    }
  }

  if (!hasBox) {
    const fitted = fitTargetAspect(0.5, 0.5, normAspect <= 1 ? normAspect : 1, normAspect <= 1 ? 1 : 1 / normAspect, normAspect);
    return { ...fitted, source: "center-fallback" };
  }

  const uw = Math.max(0.02, uMaxX - uMinX);
  const uh = Math.max(0.02, uMaxY - uMinY);
  const padX = Math.max(0.16, uw * 0.45);
  const padTop = Math.max(0.24, uh * 0.65);
  const padBottom = Math.max(0.12, uh * 0.30);

  const left = Math.max(0, uMinX - padX);
  const right = Math.min(1, uMaxX + padX);
  const top = Math.max(0, uMinY - padTop);
  const bottom = Math.min(1, uMaxY + padBottom);

  const boxW = Math.max(0.42, right - left);
  const boxH = Math.max(0.50, bottom - top);
  const cx = (left + right) / 2;
  const cy = (top + bottom) / 2;

  const fitted = fitTargetAspect(cx, cy, boxW, boxH, normAspect);
  return { ...fitted, source: "mediapipe-roi" };
}
