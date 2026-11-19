"""把 MediaPipe Tasks Vision 的 JS/WASM 与模型下载到 frontend/vendor（离线演示用）。

背景：现场 WiFi/企业网经常拦截 CDN（本机实测 jsdelivr 被 ERR_CONNECTION_RESET），
所以演示页改为加载本地资产，不依赖外网。

用法：
    python scripts/fetch_vendor.py          # 缺什么补什么
    python scripts/fetch_vendor.py --force  # 全部重下
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "frontend" / "vendor"
WASM_DIR = VENDOR / "wasm"

BASE = "https://unpkg.com/@mediapipe/tasks-vision@0.10.12"
TARGETS = [
    (BASE + "/vision_bundle.mjs", VENDOR / "vision_bundle.js"),   # 改名 .js：python http.server 不认 .mjs 的 MIME
    (BASE + "/wasm/vision_wasm_internal.js", WASM_DIR / "vision_wasm_internal.js"),
    (BASE + "/wasm/vision_wasm_internal.wasm", WASM_DIR / "vision_wasm_internal.wasm"),
    (BASE + "/wasm/vision_wasm_nosimd_internal.js", WASM_DIR / "vision_wasm_nosimd_internal.js"),
    (BASE + "/wasm/vision_wasm_nosimd_internal.wasm", WASM_DIR / "vision_wasm_nosimd_internal.wasm"),
    ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
     VENDOR / "hand_landmarker.task"),
    ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
     VENDOR / "pose_landmarker_lite.task"),
]


def remote_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "signbridge-vendor"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers.get("Content-Length", "0"))


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "signbridge-vendor"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(path, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    ok = True
    for url, path in TARGETS:
        try:
            expected = remote_size(url)
            actual = path.stat().st_size if path.exists() else 0
            if not args.force and actual == expected and actual > 0:
                print(f"skip  {path.relative_to(ROOT)}  ({actual} bytes)")
                continue
            print(f"fetch {path.relative_to(ROOT)}  (expected {expected})")
            download(url, path)
            print(f"ok    {path.relative_to(ROOT)}  ({path.stat().st_size} bytes)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"FAIL  {url} -> {exc}")
    total = sum(p.stat().st_size for p in VENDOR.rglob("*") if p.is_file())
    print(f"TOTAL {total / 1024 / 1024:.1f} MB")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
