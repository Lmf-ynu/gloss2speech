"""FastAPI 入口：按《工程实现方案》§4 提供 6 个接口。

接口列表：
    GET  /api/scenes        场景 + 词表
    POST /api/recognize     拼图 base64 + scene_id -> 手语词
    POST /api/compose       词序列 + 场景 + 历史 -> 流式句子（SSE）
    POST /api/tts           文本 -> base64 wav
    POST /api/asr           base64 音频 -> 文字
    POST /api/quick-reply   对方的话 -> 3 个快捷回复
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from .services.llm import llm_service
from .services.sis import sis_service
from .services.vision import vision_service

DATA_PATH = Path(__file__).resolve().parent / "data" / "scenes.json"
SCENES: list[dict[str, Any]] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
SCENE_BY_ID = {scene["id"]: scene for scene in SCENES}

app = FastAPI(
    title="SignBridge API",
    version="1.0.0",
    description="AI 实时手语翻译后端（按《工程实现方案》实现）：Qwen-VL 识别 + DeepSeek 造句 + SIS 语音。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_scene(scene_id: str) -> dict[str, Any]:
    return SCENE_BY_ID.get(scene_id) or SCENES[0]


# ---------------------------------------------------------------- 场景

@app.get("/api/scenes")
async def list_scenes() -> list[dict[str, Any]]:
    return SCENES


# ---------------------------------------------------------------- 手语识别

class RecognizeRequest(BaseModel):
    image: str = Field(..., description="6 帧拼图的 JPEG base64（不含 dataURL 前缀也可以）")
    scene_id: str = "hospital"


@app.post("/api/recognize")
async def recognize(req: RecognizeRequest) -> dict[str, Any]:
    scene = get_scene(req.scene_id)
    try:
        return await vision_service.recognize(req.image, scene)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"手语识别失败：{exc}") from exc


# ---------------------------------------------------------------- 造句（流式）

class ComposeRequest(BaseModel):
    words: list[str] = Field(default_factory=list)
    scene_id: str = "hospital"
    history: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/compose")
async def compose(req: ComposeRequest) -> StreamingResponse:
    scene = get_scene(req.scene_id)
    words = [word for word in req.words if word]

    async def event_stream():
        try:
            async for item in llm_service.compose_stream(
                words,
                scene["name"],
                req.history,
                scene_id=scene["id"],
            ):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            payload = {"type": "error", "message": f"造句失败：{exc}"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 语音合成

class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def tts(req: TTSRequest) -> dict[str, Any]:
    try:
        return await sis_service.tts(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"语音合成失败：{exc}") from exc


# ---------------------------------------------------------------- 语音识别

class ASRRequest(BaseModel):
    audio: str = Field(..., description="16k 单声道 wav 的 base64")


@app.post("/api/asr")
async def asr(req: ASRRequest) -> dict[str, Any]:
    try:
        return await sis_service.asr(req.audio)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"语音识别失败：{exc}") from exc


# ---------------------------------------------------------------- 快捷回复

class QuickReplyRequest(BaseModel):
    text: str
    scene_id: str = "hospital"


@app.post("/api/quick-reply")
async def quick_reply(req: QuickReplyRequest) -> dict[str, Any]:
    scene = get_scene(req.scene_id)
    try:
        replies = await llm_service.quick_reply(req.text, scene["name"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"快捷回复生成失败：{exc}") from exc
    return {"replies": replies}


# ---------------------------------------------------------------- 健康检查

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": app.version, "services": settings.describe()}


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "SignBridge API",
        "docs": "/docs",
        "endpoints": [
            "GET /api/scenes",
            "POST /api/recognize",
            "POST /api/compose (SSE)",
            "POST /api/tts",
            "POST /api/asr",
            "POST /api/quick-reply",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
