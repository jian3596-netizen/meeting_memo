"""文本清洗与切块（PRD 3.6 / 3.7）。

原则：原始转写永不覆盖（raw_text 保留），只产出 clean_text；时间戳与说话人始终保留。
"""

from __future__ import annotations

import re
from typing import Dict, List

from . import config
from .models import Segment

# 高频语气词/口头禅（仅做温和清洗，避免误删实义）
_FILLERS = [
    "嗯嗯", "嗯", "啊", "呃", "唉", "那个那个", "这个这个",
    "然后然后", "就是就是", "对对对", "对对", "哦哦",
]


def format_ts(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ts_to_seconds(ts: str) -> float:
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3], parts[-2], parts[-1]
    return h * 3600 + m * 60 + s


def clean_text(text: str) -> str:
    """去重复语气词、压缩空白。温和处理，不改变语义。"""
    t = text.strip()
    if not t:
        return t
    # 连续重复同字（如 “这这这个” -> “这个”）
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    for f in _FILLERS:
        t = t.replace(f, "")
    # 重复词组（如 “我觉得我觉得” -> “我觉得”）
    t = re.sub(r"(.{2,6}?)\1{1,}", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or text.strip()


def clean_segments(segments: List[Segment]) -> List[Segment]:
    """填充每段 clean_text；保留 raw_text。"""
    for s in segments:
        if not s.raw_text:
            s.raw_text = s.text
        s.text = clean_text(s.raw_text)
    return segments


def apply_speaker_map(segments: List[Segment], speaker_map: Dict[str, str]) -> List[Segment]:
    for s in segments:
        s.speaker_name = speaker_map.get(s.speaker)
    return segments


def speaker_label(s: Segment) -> str:
    return s.speaker_name or s.speaker


def transcript_to_text(segments: List[Segment], use_clean: bool = True) -> str:
    """拼成 `[HH:MM:SS] 说话人: 文本` 形式，喂给 LLM。"""
    lines = []
    for s in segments:
        body = s.text if use_clean else s.raw_text
        if not body.strip():
            continue
        lines.append(f"[{s.start}] {speaker_label(s)}: {body}")
    return "\n".join(lines)


def chunk_transcript(segments: List[Segment], max_chars: int = config.LLM_CHUNK_CHARS) -> List[Dict]:
    """按字符数把转写切块（保留时间范围），用于长会议 map-reduce。"""
    chunks: List[Dict] = []
    cur_lines: List[str] = []
    cur_len = 0
    cur_start = segments[0].start if segments else "00:00:00"
    cur_end = cur_start
    for s in segments:
        body = s.text if s.text.strip() else s.raw_text
        if not body.strip():
            continue
        line = f"[{s.start}] {speaker_label(s)}: {body}"
        if cur_len + len(line) > max_chars and cur_lines:
            chunks.append({"start": cur_start, "end": cur_end, "text": "\n".join(cur_lines)})
            cur_lines, cur_len, cur_start = [], 0, s.start
        cur_lines.append(line)
        cur_len += len(line) + 1
        cur_end = s.end
    if cur_lines:
        chunks.append({"start": cur_start, "end": cur_end, "text": "\n".join(cur_lines)})
    return chunks
