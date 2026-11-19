"""Vue 前端冒烟测试：无头 Chromium + 假摄像头/麦克风，验证页面、识别、流式造句、TTS、ASR。"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "reports"
result: dict = {}


def click_button(page, text: str) -> None:
    page.evaluate(
        """(label) => {
            const button = Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes(label));
            if (button) button.click();
        }""",
        text,
    )


with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
        ],
    )
    context = browser.new_context(permissions=["camera", "microphone"], viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: console_errors.append(m.type + ": " + m.text) if m.type == "error" else None)

    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    result["header_tag"] = page.locator(".header-right .el-tag").inner_text()

    click_button(page, "开启摄像头")
    page.wait_for_timeout(2500)
    result["video"] = page.evaluate(
        "() => { const v = document.querySelector('video'); return v ? { w: v.videoWidth, h: v.videoHeight, hasStream: !!v.srcObject } : null; }"
    )
    result["fps"] = page.locator(".hud b").first.inner_text()

    # 关闭自动检测，改用手动录制（假摄像头画面会持续闪烁）
    page.evaluate("() => { const s = document.querySelector('.el-switch'); if (s) s.click(); }")
    page.wait_for_timeout(800)
    click_button(page, "开始录制")
    page.wait_for_timeout(2600)
    click_button(page, "结束录制")
    page.wait_for_timeout(2500)
    result["words"] = page.locator(".words .el-tag").all_inner_texts()
    result["candidates"] = page.locator(".candidates .el-button").all_inner_texts()

    if result["words"]:
        click_button(page, "完成并造句")
        page.wait_for_timeout(3000)
    result["sentence"] = page.locator(".sentence").inner_text()
    result["alternative"] = page.locator(".alternative").inner_text() if page.locator(".alternative").count() else ""

    # 反向通道
    try:
        click_button(page, "开始说话")
        page.wait_for_timeout(2000)
        click_button(page, "停止并识别")
        page.wait_for_timeout(3000)
        result["other_text"] = page.locator(".other").inner_text() if page.locator(".other").count() else ""
        result["replies"] = page.locator(".replies .el-button").all_inner_texts()
    except Exception as exc:  # noqa: BLE001
        result["asr_error"] = str(exc)

    page.screenshot(path=str(OUT / "vue_page_check.png"), full_page=True)
    result["js_errors"] = errors[:10]
    result["console_errors"] = console_errors[:10]
    browser.close()

print(json.dumps(result, ensure_ascii=False, indent=1))
