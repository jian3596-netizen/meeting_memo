"""Pydantic 数据模型：转写片段、结构化纪要（PRD 4.2）、API 出入参。

MeetingSummary 同时用于校验 LLM 的 JSON 输出——字段缺省一律落到 "未明确"，
避免模型编造负责人/截止时间（PRD 12.3 防幻觉）。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

UNSPECIFIED = "未明确"


# ---------- 转写 ----------
class Segment(BaseModel):
    idx: int = 0
    speaker: str = "SPEAKER_00"          # 原始说话人标签
    speaker_name: Optional[str] = None   # 应用 speaker_map 后的显示名
    start: str = "00:00:00"              # HH:MM:SS（展示用）
    end: str = "00:00:00"
    start_seconds: float = 0.0           # 精确秒（前端 seek 用）
    end_seconds: float = 0.0
    text: str = ""                       # 展示文本（清洗后优先）
    raw_text: str = ""                   # 原始转写（证据来源，永不覆盖）


# ---------- 结构化纪要 ----------
class Topic(BaseModel):
    title: str = ""
    summary: str = ""
    source_time: str = UNSPECIFIED


class Decision(BaseModel):
    content: str = ""
    source_time: str = UNSPECIFIED


class Todo(BaseModel):
    owner: str = UNSPECIFIED
    task: str = ""
    deadline: str = UNSPECIFIED
    source_time: str = UNSPECIFIED


class Risk(BaseModel):
    content: str = ""
    source_time: str = UNSPECIFIED


class OpenQuestion(BaseModel):
    content: str = ""
    source_time: str = UNSPECIFIED


class MeetingSummary(BaseModel):
    title: str = "会议纪要"
    summary: str = ""
    topics: List[Topic] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    todos: List[Todo] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)


# ---------- API ----------
class CreateMeetingResponse(BaseModel):
    meeting_id: str
    status: str


class StatusResponse(BaseModel):
    meeting_id: str
    status: str
    progress: int = 0
    failed_step: Optional[str] = None
    error_message: Optional[str] = None


class RegenerateRequest(BaseModel):
    category: Optional[str] = None          # 分类名（None=保持原分类）
    custom_instruction: Optional[str] = None


class HotwordsRequest(BaseModel):
    hotwords: List[str] = Field(default_factory=list)


class CategoryItem(BaseModel):
    name: str
    prompt: str = ""


class CategoriesRequest(BaseModel):
    categories: List[CategoryItem] = Field(default_factory=list)


class VoiceprintEnrollRequest(BaseModel):
    speaker: str               # 原始说话人标签，如 SPEAKER_00
    name: str                  # 真实姓名 / 角色


class MeetingMetaRequest(BaseModel):
    """录音管理元数据（部分字段，None 表示不改）。"""
    title: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    participants: Optional[List[str]] = None
    description: Optional[str] = None
    audio_time: Optional[str] = None
