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


COMPOSE_PROMPT = """你是中国手语（CSL）实时翻译官。请把下方手语词序列整理成自然、礼貌的中文口语。
中国手语常采用"话题-评论"语序（宾语/时间前置、否定后置、省略虚词），请调整为现代汉语语序。
硬性规则：
1. 只能使用给定词序列中的核心含义，可补充必要的礼貌用语和虚词；
2. 严禁添加任何词序列中未出现的症状、疾病、药物名称、剂量、时间、金额或诊断事实；
3. 涉及医疗主诉、用药、过敏、金额、证件时 need_confirm 设为 true。

场景：{scene}
对话历史：{history}
词序列：{words}

输出格式（严格两段）：
第一行直接输出通顺的中文口语（不要加引号或前缀）；
第二行输出 JSON：{{"sentence":"同第一行","alternative":"另一句同义表达","need_confirm":false}}"""


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
