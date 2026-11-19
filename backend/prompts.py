"""提示词模板（对应《工程实现方案》§3.3 / §3.5 / §3.7）。"""

from __future__ import annotations

from typing import Any

VISION_PROMPT = """你是中国手语翻译专家。图片是一个手语动作的6帧，序号1-6表示时间顺序。
场景：{scene_name}
只能从以下词表中选择（每行：词 - 标准动作）：
{vocab_with_desc}
输出 JSON：{{"words":["词"],"confidence":0到1,"alternatives":[["候选词"]]}}
无法判断时输出 {{"words":[],"confidence":0}}。不要输出其他内容。"""


def build_vision_prompt(scene_name: str, vocab: list[dict[str, Any]]) -> str:
    lines = [f"{item['word']} - {item.get('desc', '')}" for item in vocab]
    return VISION_PROMPT.format(scene_name=scene_name, vocab_with_desc="\n".join(lines))


COMPOSE_PROMPT = """把手语词序列整理成通顺、礼貌的中文口语。
规则：只用给定词的含义，可补虚词，不添加新信息。
场景：{scene}
对话历史：{history}
词序列：{words}
输出 JSON：{{"sentence":"...","alternative":"..."}}"""


def build_compose_prompt(words: list[str], scene_name: str, history: list[dict[str, Any]] | None) -> str:
    history_text = "无"
    if history:
        turns = []
        for turn in history[-5:]:
            role = "对方" if turn.get("role") in {"other", "assistant"} else "我"
            turns.append(f"{role}：{turn.get('text', '')}")
        history_text = "；".join(turns) or "无"
    return COMPOSE_PROMPT.format(scene=scene_name, history=history_text, words="、".join(words))


QUICK_REPLY_PROMPT = """对方说："{text}"。场景：{scene}。为听障用户生成3个可能的简短回复（每个≤10字），输出JSON数组。"""


def build_quick_reply_prompt(text: str, scene_name: str) -> str:
    return QUICK_REPLY_PROMPT.format(text=text, scene=scene_name)
