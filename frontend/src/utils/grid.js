/** 抽帧拼图（对应《工程实现方案》§3.2）：6 帧 -> 3×2 网格 -> JPEG base64。 */

export function captureFrame(video) {
  const width = video.videoWidth || 1280;
  const height = video.videoHeight || 720;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(video, 0, 0, width, height);
  return { canvas: canvas, at: performance.now() };
}

/** 从 N 帧里均匀取 count 帧；不足时重复最后一帧。 */
export function pickFrames(frames, count) {
  const target = count || 6;
  if (!frames || frames.length === 0) return [];
  if (frames.length === 1) return new Array(target).fill(frames[0]);
  const picked = [];
  for (let i = 0; i < target; i += 1) {
    const index = Math.round((i * (frames.length - 1)) / (target - 1));
    picked.push(frames[index]);
  }
  return picked;
}

/**
 * 把若干帧拼成 3 列 × 2 行（默认 960×480）并在左上角标注序号 1-6。
 * 返回 JPEG dataURL（后端会自动剥掉 dataURL 前缀）。
 */
export function composeGrid(frames, options) {
  const opts = options || {};
  const frameWidth = opts.frameWidth || 320;
  const frameHeight = opts.frameHeight || 240;
  const cols = opts.cols || 3;
  const rows = opts.rows || 2;
  const quality = opts.quality != null ? opts.quality : 0.8;

  const canvas = document.createElement("canvas");
  canvas.width = frameWidth * cols;
  canvas.height = frameHeight * rows;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const picked = pickFrames(frames, cols * rows);
  picked.forEach(function (item, index) {
    const col = index % cols;
    const row = Math.floor(index / cols);
    const x = col * frameWidth;
    const y = row * frameHeight;
    const source = item && item.canvas ? item.canvas : item;
    if (source) {
      ctx.drawImage(source, x, y, frameWidth, frameHeight);
    }
    ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
    ctx.fillRect(x + 4, y + 4, 30, 24);
    ctx.fillStyle = "#38e8d0";
    ctx.font = "bold 16px Consolas, monospace";
    ctx.fillText(String(index + 1), x + 11, y + 22);
  });

  return canvas.toDataURL("image/jpeg", quality);
}
