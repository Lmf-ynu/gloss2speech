"""语音合成 / 识别：华为云 SIS。

对应《工程实现方案》§3.6 / §3.7：
    - /api/tts：文本 -> base64 wav（16k 采样率）
    - /api/asr：base64 音频 -> 文字（一句话识别，音频不超过 1 分钟）

无密钥时走 Mock：
    - TTS 返回合法静音 wav（格式与真实返回一致，前端可正常播放）
    - ASR 返回预置问句，方便前端联调快捷回复
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave

from ..config import settings


def _build_tts_request(text: str):
    """构造华为云 SIS RunTtsRequest（严格对齐 huaweicloudsdksis.v1 模型签名）。"""
    from huaweicloudsdksis.v1 import PostCustomTTSReq, RunTtsRequest, TtsConfig

    config = TtsConfig(
        audio_format="wav",
        sample_rate="16000",
        _property=settings.sis_tts_voice,
    )
    return RunTtsRequest(body=PostCustomTTSReq(text=text, config=config))


def _build_asr_request(audio_b64: str):
    """构造华为云 SIS RecognizeShortAudioRequest（严格对齐 huaweicloudsdksis.v1 模型签名）。"""
    from huaweicloudsdksis.v1 import Config, PostShortAudioReq, RecognizeShortAudioRequest

    config = Config(
        audio_format="wav",
        _property=settings.sis_asr_property,
        add_punc="yes",
    )
    return RecognizeShortAudioRequest(body=PostShortAudioReq(data=audio_b64, config=config))


class SISService:
    def __init__(self) -> None:
        self._client = None
        self._mock_asr_index = 0

    # ---------------- 真实服务 ----------------
    def _get_client(self):
        if self._client is None:
            from huaweicloudsdkcore.auth.credentials import BasicCredentials
            from huaweicloudsdksis.v1 import SisClient
            from huaweicloudsdksis.v1.region.sis_region import SisRegion

            credentials = BasicCredentials(
                settings.hw_ak,
                settings.hw_sk,
                settings.hw_project_id,
            )
            self._client = (
                SisClient.new_builder()
                .with_credentials(credentials)
                .with_region(SisRegion.value_of(settings.sis_region))
                .build()
            )
        return self._client

    def _tts_sync(self, text: str) -> dict:
        client = self._get_client()
        request = _build_tts_request(text)
        response = client.run_tts(request)
        audio = getattr(response.result, "data", None) if response.result is not None else None
        if not audio:
            raise RuntimeError("SIS TTS 未返回有效音频数据")
        if isinstance(audio, bytes):
            audio_b64 = base64.b64encode(audio).decode("ascii")
        else:
            audio_b64 = str(audio)
        return {"audio": audio_b64, "format": "wav"}

    def _asr_sync(self, audio_b64: str) -> dict:
        client = self._get_client()
        request = _build_asr_request(audio_b64)
        response = client.recognize_short_audio(request)
        text = ""
        if response.result is not None:
            text = getattr(response.result, "text", "") or ""
        return {"text": text}

    # ---------------- 对外 ----------------
    async def tts(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        if settings.use_mock("sis"):
            duration_ms = min(4000, max(300, 220 * len(text)))
            return {"audio": _silent_wav_base64(duration_ms), "format": "wav", "mock": True}
        return await asyncio.to_thread(self._tts_sync, text)

    async def asr(self, audio_base64: str) -> dict:
        audio_base64 = (audio_base64 or "").strip()
        if not audio_base64:
            raise ValueError("audio 不能为空")
        if "," in audio_base64 and audio_base64.startswith("data:"):
            audio_base64 = audio_base64.split(",", 1)[1]
        if settings.use_mock("sis"):
            canned = ["你哪里不舒服？", "请问你叫什么名字？", "请稍等一下，医生马上过来。"]
            text = canned[self._mock_asr_index % len(canned)]
            self._mock_asr_index += 1
            return {"text": text, "mock": True}
        return await asyncio.to_thread(self._asr_sync, audio_base64)


def _silent_wav_base64(duration_ms: int, sample_rate: int = 16000) -> str:
    frames = int(sample_rate * duration_ms / 1000)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


sis_service = SISService()
