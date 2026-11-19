"""提示词模板（慢路径）。核心原则：结构化、短、可计价。

翻译类推理最多 80 reasoning token；reason 字段硬限制 50 字，
避免模型把延迟和钱都花在"想太多"上（见 docs §5.4 三档 CoT）。
"""

from __future__ import annotations

import json
from typing import Any

COT_SYSTEM = """你是部署在医院/银行/政务大厅的中国手语（CSL）实时翻译官。
你的唯一任务：把手语词序列整理成自然、得体、忠于原意的中文口语。

规则：
1. 中国手语常"话题-评论"结构：宾语前置、时间前置、否定后置、省略虚词，请重排为现代汉语语序；
2. 只能使用给定词的含义，可以补虚词，禁止添加词序列中不存在的核心事实；
3. 结合场景与最近对话消解歧义（如"补办"+"身份证"），在 ambiguity 中说明选择；
4. 涉及医疗/金额/法律时 need_confirm=true；
5. 先输出一句纯文本译文（会被立即播报），再输出一个 JSON 块；
6. reasoning 字段总量不超过 80 token，reason 不超过 50 字。"""


def build_cot_messages(
    *,
    words: list[str],
    scene_id: str,
    history: list[dict[str, Any]] | None = None,
    topk: list[list[str]] | None = None,
) -> list[dict[str, str]]:
    history_text = "无"
    if history:
        lines = []
        for turn in history[-5:]:
            role = "对方" if turn.get("role") in {"other", "assistant"} else "我"
            lines.append(f"{role}：{turn.get('text', '')}")
        history_text = "；".join(lines) or "无"
    user = f"""场景：{scene_id}
最近对话：{history_text}
手语词序列（按时间顺序）：{json.dumps(words, ensure_ascii=False)}
候选歧义（若无则空）：{json.dumps(topk or [], ensure_ascii=False)}

请先输出译文纯文本，再输出 JSON：
{{"sentence":"...","alternative":"...","kept":[],"dropped":[],
  "ambiguity":[{{"slot":"","choose":"","why":""}}],
  "confidence":0.0,"need_confirm":false,"reason":"不超过50字"}}"""
    return [
        {"role": "system", "content": COT_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_cot_output(text: str, words: list[str]) -> dict[str, Any]:
    """容错解析"纯文本 + JSON"两段式输出；解析失败就退回纯文本。"""
    text = (text or "").strip()
    if not text:
        return {"sentence": "".join(words) + "。", "confidence": 0.0}
    start = text.find("{")
    end = text.rfind("}")
    data: dict[str, Any] = {}
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            data = {}
        prefix = text[:start].strip()
        if prefix and not data.get("sentence"):
            data["sentence"] = prefix.splitlines()[-1].strip()
    else:
        data["sentence"] = text
    data.setdefault("sentence", "".join(words) + "。")
    return data


QUICK_REPLY_PROMPT = """对方（健听人）说："{text}"。场景：{scene}。
为听障用户生成 3 个简短、最可能的回复，每个不超过 10 个字。
只输出 JSON 数组：["...","...","..."]"""


TEACH_SCORE_PROMPT = """你是中国手语教练。用户动作与标准动作的骨骼序列余弦相似度为 {score:.2f}。
请用一句话（不超过 30 字）指出最需要改进的一点，不要夸张鼓励。
只输出这句话。"""
