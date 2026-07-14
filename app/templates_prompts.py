"""会议模板（PRD 第 5 节）与提示词构造。

输出 JSON 结构始终是 MeetingSummary（PRD 4.2），模板只改变关注重点与摘要侧重。
核心反幻觉约束集中在 SYSTEM_PROMPT。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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


# 分类未指定 / 找不到对应 Prompt 时的兜底
DEFAULT_CATEGORY_NAME = "通用会议"
DEFAULT_CATEGORY_FOCUS = TEMPLATES["general"]["focus"]

DEFAULT_FIELD_REQUIREMENTS: Dict[str, str] = {
    "summary": "150字以内概括整场会议的背景、主要内容和核心结论。",
    "topics": "每条说明讨论背景、主要观点、结论或当前状态；除原文信息不足外，不要只写一句结论。",
    "decisions": "只记录会议中明确拍板或达成一致的事项，不要把建议、猜测写成决策。",
    "todos": "只记录明确需要后续执行的事项；负责人或截止时间不明确时填“未明确”。",
    "risks": "记录明确提到的风险、阻塞、依赖或可能影响结果的不确定因素。",
    "open_questions": "记录会议中提出但尚未解决、尚未确认或需要后续跟进的问题。",
}

DEFAULT_SECTION_TITLES: Dict[str, str] = {
    "summary": "会议摘要",
    "topics": "关键讨论点",
    "decisions": "已确认决策",
    "todos": "待办事项",
    "risks": "风险问题",
    "open_questions": "未决问题",
}


def default_sections(requirements: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    """把 v1.2 固定字段转换为 v1.3 可编辑章节。"""
    req = requirements or DEFAULT_FIELD_REQUIREMENTS
    return [
        {"id": key, "title": title, "prompt": str(req.get(key, "")).strip()}
        for key, title in DEFAULT_SECTION_TITLES.items()
    ]


def category_seed() -> List[Dict[str, Any]]:
    """内置模板作为分类库的初始种子（名称 + Prompt）。"""
    return [
        {
            "name": v["name"],
            "prompt": v["focus"],
            "requirements": dict(DEFAULT_FIELD_REQUIREMENTS),
            "sections": default_sections(),
        }
        for v in TEMPLATES.values()
    ]


SYSTEM_PROMPT = """你是一名专业的中文会议纪要助手。你的唯一信息来源是用户提供的会议转写文本。

铁律（违反任何一条都视为失败）：
1. 严禁编造转写中不存在的结论、决策、待办、负责人或截止时间。
2. 各章节中的事实或条目尽量在句末标注最相关原话的 [HH:MM:SS] 时间戳；找不到就标注 [未明确]。
3. 没有证据的章节写“未提及”，不要为了填满章节而推测。
4. 严格按给定章节配置输出，不增加、删除、改名或调整章节顺序。
5. 只输出一个 JSON 对象，不要任何解释文字，不要 ```json 代码块包裹。
"""


def _section_instruction(sections: Optional[List[Dict[str, str]]]) -> str:
    import json

    sections = sections if sections is not None else default_sections()
    lines = []
    for i, section in enumerate(sections, 1):
        prompt = (section.get("prompt") or "根据章节名称提炼相关信息").strip()
        lines.append(f'{i}. id="{section["id"]}"，标题“{section["title"]}”：{prompt}')
    configured = "\n".join(lines) if lines else "（不输出正文章节，仅生成标题）"
    example = {
        "title": "一句话会议标题",
        "sections": [
            {"id": s["id"], "title": s["title"], "content": "该章节的纪要正文"}
            for s in sections
        ],
    }
    return (
        f"章节配置（顺序必须一致）：\n{configured}\n\n"
        f"JSON 结构：\n{json.dumps(example, ensure_ascii=False, indent=2)}"
    )


def _instruction_block(
    name: str, focus: str, sections: Optional[List[Dict[str, str]]],
    custom_instruction: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> str:
    block = f"本次会议类型：{name or DEFAULT_CATEGORY_NAME}。{focus or DEFAULT_CATEGORY_FOCUS}"
    if meeting_description and meeting_description.strip():
        block += (
            "\n\n用户提供的会议背景（仅用于辅助理解，不得替代转写证据）：\n"
            f"{meeting_description.strip()}"
        )
    block += f"\n\n{_section_instruction(sections)}"
    if custom_instruction:
        block += f"\n\n补充要求：\n{custom_instruction.strip()}"
    return block


def build_summary_messages(
    transcript_text: str, name: str, focus: str,
    sections: Optional[List[Dict[str, str]]] = None,
    custom_instruction: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> List[Dict[str, str]]:
    user = (
        f"{_instruction_block(name, focus, sections, custom_instruction, meeting_description)}\n\n"
        f"以下是带时间戳和说话人的会议转写，请据此生成结构化纪要 JSON：\n\n"
        f"=== 会议转写开始 ===\n{transcript_text}\n=== 会议转写结束 ==="
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ---- 长会议 map-reduce ----
def build_map_messages(
    chunk_text: str, idx: int, total: int,
    meeting_description: Optional[str] = None,
) -> List[Dict[str, str]]:
    background = ""
    if meeting_description and meeting_description.strip():
        background = (
            "\n用户提供的会议背景（仅用于辅助理解，不得替代转写证据）：\n"
            f"{meeting_description.strip()}\n"
        )
    user = (
        f"这是一场长会议的第 {idx}/{total} 段转写。请提炼要点笔记，"
        f"每条要点保留最相关那句的 [HH:MM:SS] 时间戳，覆盖：讨论点、决策、待办（含负责人/截止）、"
        f"风险、未决问题。用简洁中文 bullet，不要编造。\n{background}\n"
        f"=== 转写片段开始 ===\n{chunk_text}\n=== 转写片段结束 ==="
    )
    return [
        {"role": "system", "content": "你是会议纪要助手，只基于给定片段提炼要点，保留时间戳，不编造。"},
        {"role": "user", "content": user},
    ]


def build_reduce_messages(
    notes: str, name: str, focus: str,
    sections: Optional[List[Dict[str, str]]] = None,
    custom_instruction: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> List[Dict[str, str]]:
    user = (
        f"{_instruction_block(name, focus, sections, custom_instruction, meeting_description)}\n\n"
        f"以下是同一场会议各片段的要点笔记（已带时间戳），请合并去重，生成最终结构化纪要 JSON：\n\n"
        f"=== 要点笔记开始 ===\n{notes}\n=== 要点笔记结束 ==="
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
