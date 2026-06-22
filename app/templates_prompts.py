"""会议模板（PRD 第 5 节）与提示词构造。

输出 JSON 结构始终是 MeetingSummary（PRD 4.2），模板只改变关注重点与摘要侧重。
核心反幻觉约束集中在 SYSTEM_PROMPT。
"""

from __future__ import annotations

from typing import Dict, List, Optional

TEMPLATES: Dict[str, Dict[str, str]] = {
    "general": {
        "name": "通用会议",
        "focus": "关注：会议摘要、关键讨论、结论、待办、风险、未决问题。",
    },
    "project": {
        "name": "项目会议",
        "focus": (
            "关注：项目进展、当前阻塞、关键决策、各事项负责人与截止时间、"
            "下次会议前要完成的动作。todos 要尽量明确 owner 和 deadline。"
        ),
    },
    "customer": {
        "name": "客户拜访",
        "focus": (
            "关注：客户背景、客户需求、客户异议、预算与时间线、竞品信息、下一步跟进动作。"
            "把跟进动作放进 todos。"
        ),
    },
    "technical": {
        "name": "技术评审",
        "focus": (
            "关注：方案背景、技术路线、争议点、决策结果、技术风险、后续验证项。"
            "争议点放 open_questions，验证项放 todos。"
        ),
    },
    "daily": {
        "name": "日常记录",
        "focus": (
            "关注：按时间顺序如实记录谈了什么、提到的信息和结论。"
            "不必强行归纳决策/待办，只有明确提到要做的事才放进 todos；"
            "summary 用平实口吻概括整段内容。"
        ),
    },
    "regular": {
        "name": "例会",
        "focus": (
            "关注：各人/各条线的进展同步、上次待办的完成情况、本期新待办、遇到的阻塞、"
            "需要协调或下次跟进的事项。todos 要尽量明确 owner 和 deadline，阻塞放 risks。"
        ),
    },
}


def template_name(template_type: str) -> str:
    return TEMPLATES.get(template_type, TEMPLATES["general"])["name"]


SYSTEM_PROMPT = """你是一名专业的中文会议纪要助手。你的唯一信息来源是用户提供的会议转写文本。

铁律（违反任何一条都视为失败）：
1. 严禁编造转写中不存在的结论、决策、待办、负责人或截止时间。
2. 每一条 topics / decisions / todos / risks / open_questions 都必须带 source_time，
   取自转写里最相关那句话开头的 [HH:MM:SS] 时间戳；找不到就填 "未明确"。
3. todos 中：负责人不明确填 owner="未明确"；截止时间不明确填 deadline="未明确"。
4. 只输出一个 JSON 对象，不要任何解释文字，不要 ```json 代码块包裹。

JSON 结构（严格遵守字段名）：
{
  "title": "一句话会议标题",
  "summary": "150字以内的整体摘要",
  "topics": [{"title": "...", "summary": "...", "source_time": "HH:MM:SS"}],
  "decisions": [{"content": "...", "source_time": "HH:MM:SS"}],
  "todos": [{"owner": "...", "task": "...", "deadline": "...", "source_time": "HH:MM:SS"}],
  "risks": [{"content": "...", "source_time": "HH:MM:SS"}],
  "open_questions": [{"content": "...", "source_time": "HH:MM:SS"}]
}
"""


def _instruction_block(template_type: str, custom_instruction: Optional[str]) -> str:
    tpl = TEMPLATES.get(template_type, TEMPLATES["general"])
    block = f"本次会议类型：{tpl['name']}。{tpl['focus']}"
    if custom_instruction:
        block += f"\n额外要求：{custom_instruction.strip()}"
    return block


def build_summary_messages(
    transcript_text: str, template_type: str, custom_instruction: Optional[str] = None
) -> List[Dict[str, str]]:
    user = (
        f"{_instruction_block(template_type, custom_instruction)}\n\n"
        f"以下是带时间戳和说话人的会议转写，请据此生成结构化纪要 JSON：\n\n"
        f"=== 会议转写开始 ===\n{transcript_text}\n=== 会议转写结束 ==="
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ---- 长会议 map-reduce ----
def build_map_messages(chunk_text: str, idx: int, total: int) -> List[Dict[str, str]]:
    user = (
        f"这是一场长会议的第 {idx}/{total} 段转写。请提炼要点笔记，"
        f"每条要点保留最相关那句的 [HH:MM:SS] 时间戳，覆盖：讨论点、决策、待办（含负责人/截止）、"
        f"风险、未决问题。用简洁中文 bullet，不要编造。\n\n"
        f"=== 转写片段开始 ===\n{chunk_text}\n=== 转写片段结束 ==="
    )
    return [
        {"role": "system", "content": "你是会议纪要助手，只基于给定片段提炼要点，保留时间戳，不编造。"},
        {"role": "user", "content": user},
    ]


def build_reduce_messages(
    notes: str, template_type: str, custom_instruction: Optional[str] = None
) -> List[Dict[str, str]]:
    user = (
        f"{_instruction_block(template_type, custom_instruction)}\n\n"
        f"以下是同一场会议各片段的要点笔记（已带时间戳），请合并去重，生成最终结构化纪要 JSON：\n\n"
        f"=== 要点笔记开始 ===\n{notes}\n=== 要点笔记结束 ==="
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
