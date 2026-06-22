"""LLM 结构化纪要生成（PRD 第 4 节）。

通义千问走 OpenAI 兼容端点。短转写单次抽取；长转写 map-reduce（分段提炼→合并）。
输出用 MeetingSummary 校验，解析失败重试一次。

Fake provider：从转写里取标题，产出一份可展示的结构化纪要，便于无 key 跑通。
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from . import config, templates_prompts
from .models import MeetingSummary, Segment
from .textproc import chunk_transcript, transcript_to_text

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> dict:
    t = text.strip()
    t = _JSON_FENCE.sub("", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 容错：截取第一个 { 到最后一个 }
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(t[start : end + 1])
        raise


class QwenLLM:
    def __init__(self) -> None:
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法调用通义千问")
        from openai import OpenAI
        self._client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.DASHSCOPE_LLM_BASE_URL,
        )

    def _chat(self, messages: List[dict], json_mode: bool = True) -> str:
        kwargs = {"model": config.LLM_MODEL, "messages": messages, "temperature": 0.2}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _chat_to_summary(self, messages: List[dict]) -> MeetingSummary:
        for attempt in range(2):
            raw = self._chat(messages, json_mode=True)
            try:
                return MeetingSummary.model_validate(_extract_json(raw))
            except Exception:
                if attempt == 0:
                    messages = messages + [{
                        "role": "user",
                        "content": "你上一条输出不是合法的目标 JSON。请只输出符合结构的 JSON 对象，不要任何额外文字。",
                    }]
                    continue
                raise

    def summarize(
        self, segments: List[Segment], cat_name: str, cat_focus: str,
        custom_instruction: Optional[str] = None,
    ) -> MeetingSummary:
        text = transcript_to_text(segments, use_clean=True)
        if len(text) <= config.LLM_SINGLE_PASS_MAX_CHARS:
            messages = templates_prompts.build_summary_messages(
                text, cat_name, cat_focus, custom_instruction
            )
            return self._chat_to_summary(messages)

        # map-reduce
        chunks = chunk_transcript(segments)
        notes_parts = []
        for i, ch in enumerate(chunks, 1):
            note = self._chat(
                templates_prompts.build_map_messages(ch["text"], i, len(chunks)),
                json_mode=False,
            )
            notes_parts.append(f"[片段{i} {ch['start']}~{ch['end']}]\n{note}")
        notes = "\n\n".join(notes_parts)
        return self._chat_to_summary(
            templates_prompts.build_reduce_messages(notes, cat_name, cat_focus, custom_instruction)
        )


class FakeLLM:
    def summarize(
        self, segments: List[Segment], cat_name: str, cat_focus: str,
        custom_instruction: Optional[str] = None,
    ) -> MeetingSummary:
        first = segments[0].text if segments else "会议"
        title = (first[:18] + "…") if len(first) > 18 else first
        return MeetingSummary(
            title=f"[{cat_name or '通用会议'}] {title}",
            summary="（示例纪要）本次会议讨论了前后端接口设计，倾向先固定通讯格式再并行实现，"
                    "并就本地 ASR 部署位置留有未决问题。",
            topics=[{"title": "前后端接口设计", "summary": "倾向先固定 API 再并行实现。", "source_time": "00:00:28"}],
            decisions=[{"content": "先定义统一 API v0.1，再实现本地/云/多 Agent 后端。", "source_time": "00:00:28"}],
            todos=[{"owner": "未明确", "task": "整理前后端接口 v0.1 草案并发群", "deadline": "本周五", "source_time": "00:00:39"}],
            risks=[{"content": "接口过早固定可能限制后续多 Agent 扩展。", "source_time": "00:00:19"}],
            open_questions=[{"content": "本地 ASR 部署在板子还是先用云端？", "source_time": "00:00:48"}],
        )


def get_llm():
    if config.llm_is_fake():
        return FakeLLM()
    return QwenLLM()
