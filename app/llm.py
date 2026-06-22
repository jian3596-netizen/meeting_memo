"""LLM 结构化纪要生成（PRD 第 4 节）。

总结 LLM 走 OpenAI 兼容端点：URL + API Key + 模型名均由 .env 提供（默认 DeepSeek）。
短转写单次抽取；长转写 map-reduce（分段提炼→合并）。
输出用 MeetingSummary 校验，解析失败重试一次。
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


class OpenAICompatLLM:
    """OpenAI 兼容端点（DeepSeek / 通义 / 任意兼容服务），由 .env 指定 URL + Key + 模型。"""

    def __init__(self) -> None:
        if not config.LLM_API_KEY:
            raise RuntimeError(
                "缺少 LLM_API_KEY，无法调用总结 LLM（请在 .env 配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）"
            )
        from openai import OpenAI
        self._client = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
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


def get_llm():
    """总结 LLM 始终走 OpenAI 兼容端点（配置见 .env）。"""
    return OpenAICompatLLM()
