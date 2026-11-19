"""无人值守验证 camera.html：真实摄像头 / 假摄像头两种模式，检查 getUserMedia + 页面状态。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"
URL = "http://127.0.0.1:5173/camera.html"


def run(fake: bool) -> dict:
    args = [
        "--use-fake-ui-for-media-stream",          # 自动允许权限弹窗
        "--autoplay-policy=no-user-gesture-required",
    ]
    if fake:
        args.append("--use-fake-device-for-media-stream")  # 使用 Chromium 内置假摄像头

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=args)
        ctx = browser.new_context(permissions=["camera"], viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        logs: list[str] = []
        errors: list[str] = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)

        deadline = time.time() + 45
        status = ""
        while time.time() < deadline:
            status = page.inner_text("#status")
            if "就绪" in status or "失败" in status:
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(4000)  # 让检测循环跑几秒，拿 FPS

        info = page.evaluate(
            """() => ({
                status: document.getElementById('status').textContent,
                videoW: document.getElementById('video').videoWidth,
                videoH: document.getElementById('video').videoHeight,
                fps: document.getElementById('fps').textContent,
                ws: document.getElementById('wsState').textContent,
                vad: document.getElementById('vadState').textContent,
                templates: document.getElementById('templates').textContent,
            })"""
        )
        shot = OUT / ("camera_page_" + ("fake" if fake else "real") + ".png")
        page.screenshot(path=str(shot))
        browser.close()
        return {
            "mode": "fake" if fake else "real",
            **info,
            "screenshot": shot.name,
            "js_errors": errors[:10],
            "console_tail": logs[-12:],
        }


def main() -> None:
    results = []
    for fake in (False, True):
        try:
            results.append(run(fake))
        except Exception as exc:  # noqa: BLE001
            results.append({"mode": "fake" if fake else "real", "fatal": f"{type(exc).__name__}: {exc}"})
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
