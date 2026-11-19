"""摄像头采集自检：枚举设备 -> 打开 -> 抓帧 -> 测分辨率/帧率/首帧耗时 -> 存图。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "reports"
OUT.mkdir(parents=True, exist_ok=True)


def try_open(index: int, backend_name: str, backend: int) -> dict | None:
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None
    # 先读掉若干帧，很多 UVC 相机前几帧是黑的/未就绪
    t0 = time.perf_counter()
    frame = None
    for _ in range(15):
        ok, f = cap.read()
        if ok and f is not None and f.size:
            frame = f
            break
    first_ms = (time.perf_counter() - t0) * 1000.0
    if frame is None:
        cap.release()
        return None
    h, w = frame.shape[:2]
    # 测 30 帧的抓取帧率
    t1 = time.perf_counter()
    got = 0
    for _ in range(30):
        ok, _ = cap.read()
        if ok:
            got += 1
    elapsed = time.perf_counter() - t1
    fps = got / elapsed if elapsed > 0 else 0.0
    bright = float(frame.mean())
    cap.release()
    return {
        "index": index, "backend": backend_name, "first_frame_ms": round(first_ms, 1),
        "width": int(w), "height": int(h), "fps_measured": round(fps, 1),
        "mean_brightness": round(bright, 1), "frame": frame,
    }


def main() -> None:
    results = []
    for index in (0, 1):
        for backend_name, backend in (("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("ANY", cv2.CAP_ANY)):
            try:
                res = try_open(index, backend_name, backend)
            except Exception as exc:  # noqa: BLE001
                print(f"[{index}/{backend_name}] 异常：{exc}")
                continue
            if res:
                results.append(res)
                print(json.dumps({k: v for k, v in res.items() if k != "frame"}, ensure_ascii=False))
                break  # 同一 index 打开成功就不再试其他后端

    if not results:
        print("FAIL: 没有成功打开的摄像头")
        return

    best = results[0]
    frame = best["frame"]
    cv2.imwrite(str(OUT / "camera_check_full.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
    cv2.imwrite(str(OUT / "camera_check.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("SAVED", OUT / "camera_check.jpg")


if __name__ == "__main__":
    main()
