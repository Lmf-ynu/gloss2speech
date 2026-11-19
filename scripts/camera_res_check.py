"""测摄像头支持的分辨率/编码（演示页请求 1280x720）。"""
import time
from pathlib import Path
import cv2

OUT = Path(__file__).resolve().parent.parent / "reports"

def grab(label, width, height, fourcc=None):
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(label, "OPEN FAIL")
        return None
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    for _ in range(20):
        ok, frame = cap.read()
        if ok and frame is not None:
            break
    if not ok:
        cap.release()
        print(label, "READ FAIL")
        return None
    h, w = frame.shape[:2]
    actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    code = "".join(chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4))
    t0 = time.perf_counter()
    got = sum(1 for _ in range(30) if cap.read()[0])
    fps = got / (time.perf_counter() - t0)
    path = OUT / ("camera_" + label + ".jpg")
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    cap.release()
    print(f"{label}: {w}x{h} fourcc={code} fps={fps:.1f} saved={path.name}")
    return path

grab("720p_mjpg", 1280, 720, "MJPG")
grab("720p_default", 1280, 720)
grab("native", 0, 0)
