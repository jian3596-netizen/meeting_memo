"""SQLite 数据层（PRD 第 6 节四张表）。

用 stdlib sqlite3 + WAL，单机零依赖。后台线程会并发写，故每次操作开新连接、
开启 WAL，避免跨线程共享连接。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config
from .models import MeetingSummary, Segment


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT,
    original_filename TEXT,
    audio_path TEXT,
    processed_path TEXT,
    duration_sec REAL DEFAULT 0,
    status TEXT DEFAULT 'uploaded',
    template_type TEXT DEFAULT 'general',
    progress INTEGER DEFAULT 0,
    failed_step TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    speaker_map TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    idx INTEGER,
    speaker TEXT,
    start_ms INTEGER,
    end_ms INTEGER,
    raw_text TEXT,
    clean_text TEXT,
    created_at TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_seg_meeting ON transcript_segments(meeting_id, idx);

CREATE TABLE IF NOT EXISTS meeting_summaries (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    summary_json TEXT,
    summary_markdown TEXT,
    model_name TEXT,
    prompt_version TEXT,
    created_at TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_tasks (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    owner TEXT,
    task TEXT,
    deadline TEXT,
    status TEXT DEFAULT 'open',
    source_time TEXT,
    created_at TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


def init_db() -> None:
    with closing(get_conn()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


# ---------- meetings ----------
def create_meeting(
    *, title: str, original_filename: str, audio_path: str, template_type: str
) -> str:
    mid = new_id()
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute(
            """INSERT INTO meetings
               (id, title, original_filename, audio_path, status, template_type,
                progress, speaker_map, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'uploaded', ?, 0, '{}', ?, ?)""",
            (mid, title, original_filename, audio_path, template_type, now, now),
        )
        conn.commit()
    return mid


def update_meeting(mid: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with closing(get_conn()) as conn:
        conn.execute(f"UPDATE meetings SET {cols} WHERE id=?", (*fields.values(), mid))
        conn.commit()


def set_status(mid: str, status: str, progress: Optional[int] = None) -> None:
    fields: Dict[str, Any] = {"status": status}
    if progress is not None:
        fields["progress"] = progress
    update_meeting(mid, **fields)


def mark_failed(mid: str, step: str, message: str) -> None:
    update_meeting(
        mid, status="failed", failed_step=step, error_message=message[:2000]
    )


def get_meeting(mid: str) -> Optional[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def list_meetings() -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM meetings ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_meeting(mid: str) -> None:
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM meetings WHERE id=?", (mid,))
        conn.commit()


def get_speaker_map(mid: str) -> Dict[str, str]:
    m = get_meeting(mid)
    if not m:
        return {}
    try:
        return json.loads(m.get("speaker_map") or "{}")
    except json.JSONDecodeError:
        return {}


def set_speaker_map(mid: str, mapping: Dict[str, str]) -> None:
    update_meeting(mid, speaker_map=json.dumps(mapping, ensure_ascii=False))


# ---------- transcript_segments ----------
def save_segments(mid: str, segments: List[Segment]) -> None:
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM transcript_segments WHERE meeting_id=?", (mid,))
        conn.executemany(
            """INSERT INTO transcript_segments
               (id, meeting_id, idx, speaker, start_ms, end_ms, raw_text, clean_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    new_id(), mid, s.idx, s.speaker,
                    int(s.start_seconds * 1000), int(s.end_seconds * 1000),
                    s.raw_text, s.text, now,
                )
                for s in segments
            ],
        )
        conn.commit()


def get_segment_rows(mid: str) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_segments WHERE meeting_id=? ORDER BY idx",
            (mid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- meeting_summaries ----------
def save_summary(mid: str, summary: MeetingSummary, markdown: str, model_name: str) -> None:
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM meeting_summaries WHERE meeting_id=?", (mid,))
        conn.execute(
            """INSERT INTO meeting_summaries
               (id, meeting_id, summary_json, summary_markdown, model_name, prompt_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(), mid,
                summary.model_dump_json(),
                markdown, model_name, config.PROMPT_VERSION, _now(),
            ),
        )
        conn.commit()


def get_summary_row(mid: str) -> Optional[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM meeting_summaries WHERE meeting_id=?", (mid,)
        ).fetchone()
    return dict(row) if row else None


# ---------- meeting_tasks ----------
def save_tasks(mid: str, summary: MeetingSummary) -> None:
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM meeting_tasks WHERE meeting_id=?", (mid,))
        conn.executemany(
            """INSERT INTO meeting_tasks
               (id, meeting_id, owner, task, deadline, status, source_time, created_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            [
                (new_id(), mid, t.owner, t.task, t.deadline, t.source_time, now)
                for t in summary.todos
            ],
        )
        conn.commit()


def get_task_rows(mid: str) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM meeting_tasks WHERE meeting_id=? ORDER BY created_at",
            (mid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- app_settings / 热词 ----------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _now()),
        )
        conn.commit()


def get_hotwords() -> List[str]:
    raw = get_setting("hotwords", "[]") or "[]"
    try:
        words = json.loads(raw)
        return [str(w).strip() for w in words if str(w).strip()]
    except json.JSONDecodeError:
        return []


def set_hotwords(words: List[str]) -> List[str]:
    cleaned = []
    seen = set()
    for w in words:
        w = str(w).strip()
        if w and w not in seen:
            seen.add(w)
            cleaned.append(w)
    set_setting("hotwords", json.dumps(cleaned, ensure_ascii=False))
    return cleaned
