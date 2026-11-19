"""造句 / 快捷回复：华为云 MaaS DeepSeek（OpenAI 兼容，stream=True）。

对应《工程实现方案》§3.5 / §3.7：
    - /api/compose：词序列 + 场景 + 历史 -> 流式返回句子（边收边显示）
    - /api/quick-reply：对方的话 -> 3 个快捷回复

无密钥时走 Mock：按规则生成句子并分块"流式"吐出，保证前端体验一致。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from ..config import settings
from ..prompts import build_compose_prompt, build_quick_reply_prompt


class _SentenceExtractor:
    """从流式 JSON 文本里增量提取 "sentence" 字段，只把句子内容推给前端。"""

    def __init__(self) -> None:
        self._buffer = ""
        self._pos = 0
        self._started = False
        self._finished = False
        self.text = ""

    def feed(self, chunk: str) -> str:
        if self._finished or not chunk:
            return ""
        self._buffer += chunk
        if not self._started:
            match = re.search(r'"sentence"\s*:\s*"', self._buffer)
            if not match:
                return ""
            self._pos = match.end()
            self._started = True
        out: list[str] = []
        escaping = {"\"": "\"", "n": "\n", "t": "\t", "\\": "\\", "/": "/"}
        while self._pos < len(self._buffer):
            char = self._buffer[self._pos]
            if char == "\\":
                if self._pos + 1 >= len(self._buffer):
                    break
                nxt = self._buffer[self._pos + 1]
                out.append(escaping.get(nxt, nxt))
                self._pos += 2
                continue
            if char == '"':
                self._finished = True
                self._pos += 1
                break
            out.append(char)
            self._pos += 1
        piece = "".join(out)
        self.text += piece
        return piece


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class LLMService:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=settings.maas_base_url,
                api_key=settings.maas_api_key,
                timeout=30.0,
            )
        return self._client

    # ---------------- compose ----------------
    async def compose_stream(
        self,
        words: list[str],
        scene_name: str,
        history: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        if settings.use_mock("llm"):
            async for item in self._mock_compose_stream(words, scene_name):
                yield item
            return

        prompt = build_compose_prompt(words, scene_name, history)
        client = self._get_client()
        stream = await client.chat.completions.create(
            model=settings.maas_llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
            stream=True,
        )
        extractor = _SentenceExtractor()
        raw_parts: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            raw_parts.append(delta)
            piece = extractor.feed(delta)
            if piece:
                yield {"type": "delta", "text": piece}

        raw = "".join(raw_parts)
        parsed = _parse_json_object(raw)
        sentence = parsed.get("sentence") or extractor.text or ("".join(words) + "。")
        alternative = parsed.get("alternative", "")
        yield {"type": "done", "sentence": sentence, "alternative": alternative}

    async def _mock_compose_stream(self, words: list[str], scene_name: str) -> AsyncIterator[dict[str, Any]]:
        sentence, alternative = self._mock_sentence(words, scene_name)
        for index in range(0, len(sentence), 3):
            await asyncio.sleep(0.02)
            yield {"type": "delta", "text": sentence[index : index + 3]}
        yield {"type": "done", "sentence": sentence, "alternative": alternative}

    @staticmethod
    def _mock_sentence(words: list[str], scene_name: str) -> tuple[str, str]:
        cleaned = [word for word in words if word]
        if not cleaned:
            return "你好，请问需要什么帮助？", "请问有什么事？"
        joined = "".join(cleaned)
        rules = [
            (("头疼", "两天"), "医生，我头疼两天了。", "医生，我头疼，已经两天了。"),
            (("头疼",), "医生，我头疼。", "医生，我的头很疼。"),
            (("发烧",), "医生，我发烧了。", "医生，我好像发烧了。"),
            (("肚子疼",), "医生，我肚子疼。", "医生，我的肚子很疼。"),
            (("挂号",), "你好，我要挂号。", "您好，我想挂一个号。"),
            (("药",), "医生，请给我开点药。", "医生，我需要拿药。"),
            (("厕所",), "请问洗手间在哪里？", "请问厕所在哪里？"),
            (("多少钱",), "请问一共多少钱？", "这个要多少钱？"),
            (("取钱",), "你好，我要取钱。", "您好，我想取一些现金。"),
            (("存钱",), "你好，我要存钱。", "您好，我想存钱。"),
            (("办证", "身份证"), "你好，我要补办身份证。", "您好，我的身份证需要补办。"),
            (("社保",), "你好，我要办社保。", "您好，我想办理社保业务。"),
            (("材料",), "请问需要带什么材料？", "请问需要准备哪些材料？"),
            (("吃饭",), "一起去吃饭吗？", "我们去吃饭吧。"),
            (("回家",), "我要回家了。", "我先回家了。"),
        ]
        for pattern, sentence_text, alternative_text in rules:
            if all(word in joined for word in pattern):
                return sentence_text, alternative_text
        return "，".join(cleaned) + "。", "".join(cleaned) + "。"

    # ---------------- quick reply ----------------
    async def quick_reply(self, text: str, scene_name: str) -> list[str]:
        if settings.use_mock("llm"):
            await asyncio.sleep(0.05)
            return self._mock_quick_reply(text)

        prompt = build_quick_reply_prompt(text, scene_name)
        client = self._get_client()
        resp = await client.chat.completions.create(
            model=settings.maas_llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=120,
        )
        raw = resp.choices[0].message.content or ""
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end > start:
            try:
                replies = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                replies = []
            if isinstance(replies, list):
                cleaned = [str(item).strip()[:12] for item in replies if str(item).strip()]
                if cleaned:
                    return cleaned[:3]
        return ["好的，谢谢。", "请再说一遍。", "我需要帮助。"]

    @staticmethod
    def _mock_quick_reply(text: str) -> list[str]:
        if re.search(r"哪里不舒服|怎么不舒服|什么症状", text):
            return ["我头疼", "我发烧", "我肚子疼"]
        if re.search(r"姓名|名字|怎么称呼", text):
            return ["我叫王明", "请再说一遍", "我写字给你看"]
        if re.search(r"多少|几个月|几天", text):
            return ["两天了", "一个星期", "刚刚开始"]
        if re.search(r"带了吗|材料|证件", text):
            return ["带了", "没有带", "我去拿"]
        return ["好的，谢谢。", "请再说一遍。", "我需要帮助。"]


llm_service = LLMService()
