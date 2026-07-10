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
    category TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    participants TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    audio_time TEXT DEFAULT '',
    spk_num INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS voiceprints (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,          -- 一个人可有多份模板（不同设备/来源），故不唯一
    embedding TEXT NOT NULL,     -- JSON：192 维 L2 归一化声纹中心
    sample_count INTEGER,        -- 由多少条语音聚合而来
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_voiceprints_name ON voiceprints(name);

CREATE TABLE IF NOT EXISTS correction_events (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    before_text TEXT,
    after_text TEXT,
    candidates_json TEXT DEFAULT '[]',
    created_at TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_correction_events_meeting ON correction_events(meeting_id, created_at);

CREATE TABLE IF NOT EXISTS correction_rules (
    id TEXT PRIMARY KEY,
    wrong_text TEXT NOT NULL,
    correct_text TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0,
    confirmed_count INTEGER DEFAULT 0,
    rejected_count INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0,
    enabled INTEGER DEFAULT 0,
    examples TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(wrong_text, correct_text)
);
CREATE INDEX IF NOT EXISTS idx_correction_rules_enabled ON correction_rules(enabled, wrong_text);
"""


def init_db() -> None:
    with closing(get_conn()) as conn:
        _migrate_voiceprints(conn)
        conn.executescript(SCHEMA)
        _migrate_meetings_meta(conn)
        conn.commit()
    _seed_categories_if_empty()
    _migrate_category_requirements_once()


def _migrate_meetings_meta(conn: sqlite3.Connection) -> None:
    """给旧库的 meetings 表补上录音管理用的元数据列（分类/标签/人员/描述）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
    add = {
        "category": "TEXT DEFAULT ''",
        "tags": "TEXT DEFAULT '[]'",
        "participants": "TEXT DEFAULT '[]'",
        "description": "TEXT DEFAULT ''",
        "audio_time": "TEXT DEFAULT ''",
        "spk_num": "INTEGER DEFAULT 0",
    }
    for col, ddl in add.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {ddl}")
    conn.commit()


def _migrate_voiceprints(conn: sqlite3.Connection) -> None:
    """旧版 voiceprints.name 有 UNIQUE 约束（单声纹）；多模板需去掉。原地重建保留数据。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='voiceprints'"
    ).fetchone()
    if row and "UNIQUE" in (row["sql"] or ""):
        conn.executescript(
            """
            ALTER TABLE voiceprints RENAME TO _voiceprints_old;
            CREATE TABLE voiceprints (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, embedding TEXT NOT NULL,
                sample_count INTEGER, created_at TEXT, updated_at TEXT
            );
            INSERT INTO voiceprints SELECT id, name, embedding, sample_count, created_at, updated_at
                FROM _voiceprints_old;
            DROP TABLE _voiceprints_old;
            """
        )
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


# ---------- correction library ----------
def save_correction_event(
    mid: str, before_text: str, after_text: str, candidates: List[Dict[str, Any]]
) -> None:
    with closing(get_conn()) as conn:
        conn.execute(
            """INSERT INTO correction_events
               (id, meeting_id, before_text, after_text, candidates_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                new_id(), mid, before_text, after_text,
                json.dumps(candidates, ensure_ascii=False), _now(),
            ),
        )
        conn.commit()


def upsert_correction_rule(
    wrong_text: str,
    correct_text: str,
    *,
    confidence: float,
    example: Dict[str, Any],
    auto_enable_hits: int = 3,
) -> Dict[str, Any]:
    now = _now()
    wrong_text = wrong_text.strip()
    correct_text = correct_text.strip()
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM correction_rules WHERE wrong_text=? AND correct_text=?",
            (wrong_text, correct_text),
        ).fetchone()
        if row:
            examples = _json_list(row["examples"])
            examples.append(example)
            examples = examples[-5:]
            hit_count = int(row["hit_count"] or 0) + 1
            merged_conf = max(float(row["confidence"] or 0), confidence)
            enabled = int(row["enabled"] or 0)
            if not enabled and hit_count >= auto_enable_hits and merged_conf >= 0.25:
                enabled = 1
            conn.execute(
                """UPDATE correction_rules
                   SET hit_count=?, confirmed_count=?, confidence=?, enabled=?,
                       examples=?, updated_at=?
                   WHERE id=?""",
                (
                    hit_count, hit_count, merged_conf, enabled,
                    json.dumps(examples, ensure_ascii=False), now, row["id"],
                ),
            )
            rid = row["id"]
        else:
            enabled = 1 if auto_enable_hits <= 1 and confidence >= 0.25 else 0
            conn.execute(
                """INSERT INTO correction_rules
                   (id, wrong_text, correct_text, hit_count, confirmed_count,
                    rejected_count, confidence, enabled, examples, created_at, updated_at)
                   VALUES (?, ?, ?, 1, 1, 0, ?, ?, ?, ?, ?)""",
                (
                    new_id(), wrong_text, correct_text, confidence, enabled,
                    json.dumps([example], ensure_ascii=False), now, now,
                ),
            )
            rid = conn.execute(
                "SELECT id FROM correction_rules WHERE wrong_text=? AND correct_text=?",
                (wrong_text, correct_text),
            ).fetchone()["id"]
            hit_count = 1
            merged_conf = confidence
        conn.commit()
    return {
        "id": rid,
        "wrong_text": wrong_text,
        "correct_text": correct_text,
        "hit_count": hit_count,
        "confidence": merged_conf,
        "enabled": bool(enabled),
    }


def get_enabled_correction_rules() -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            """SELECT * FROM correction_rules
               WHERE enabled=1 AND wrong_text<>'' AND correct_text<>''
               ORDER BY LENGTH(wrong_text) DESC, hit_count DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def list_correction_rules(limit: int = 200) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            """SELECT * FROM correction_rules
               ORDER BY enabled DESC, hit_count DESC, updated_at DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def _json_list(raw: Any) -> List[Any]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


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


SUMMARY_FIELD_KEYS = ("summary", "topics", "decisions", "todos", "risks", "open_questions")


def _clean_requirements(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw.get(key, "")).strip()
        for key in SUMMARY_FIELD_KEYS
        if str(raw.get(key, "")).strip()
    }


# ---------- 分类库（名称 + 总结 Prompt + 字段要求） ----------
def get_categories() -> List[Dict[str, Any]]:
    raw = get_setting("categories")
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: List[Dict[str, Any]] = []
    for c in data:
        name = str(c.get("name", "")).strip()
        if name:
            out.append({
                "name": name,
                "prompt": str(c.get("prompt", "")).strip(),
                "requirements": _clean_requirements(c.get("requirements")),
            })
    return out


def set_categories(cats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for c in cats:
        name = str(c.get("name", "")).strip()
        if name and name not in seen:
            seen.add(name)
            cleaned.append({
                "name": name,
                "prompt": str(c.get("prompt", "")).strip(),
                "requirements": _clean_requirements(c.get("requirements")),
            })
    set_setting("categories", json.dumps(cleaned, ensure_ascii=False))
    return cleaned


def get_category_prompt(name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    for c in get_categories():
        if c["name"] == name:
            return c["prompt"] or None
    return None


def get_category_requirements(name: str) -> Dict[str, str]:
    name = (name or "").strip()
    if not name:
        return {}
    for c in get_categories():
        if c["name"] == name:
            return dict(c.get("requirements") or {})
    return {}


def _seed_categories_if_empty() -> None:
    if get_setting("categories") is None:
        from .templates_prompts import category_seed
        set_setting("categories", json.dumps(category_seed(), ensure_ascii=False))


def _migrate_category_requirements_once() -> None:
    if get_setting("category_requirements_v1") == "done":
        return
    from .templates_prompts import DEFAULT_FIELD_REQUIREMENTS

    cats = get_categories()
    changed = False
    for c in cats:
        if not c.get("requirements"):
            c["requirements"] = dict(DEFAULT_FIELD_REQUIREMENTS)
            changed = True
    if changed:
        set_categories(cats)
    set_setting("category_requirements_v1", "done")


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


# ---------- voiceprints / 声纹（一人多模板） ----------
def list_voiceprints() -> List[Dict[str, Any]]:
    """按人聚合的概要，给前端展示：[{name, count(模板数), sample_count(总样本)}]。"""
    with closing(get_conn()) as conn:
        rows = conn.execute(
            """SELECT name, COUNT(*) AS count, COALESCE(SUM(sample_count), 0) AS sample_count
               FROM voiceprints GROUP BY name ORDER BY name"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_voiceprints() -> List[Dict[str, Any]]:
    """全部模板（含向量），给匹配用：[{name, emb:list[float], sample_count}]。"""
    with closing(get_conn()) as conn:
        rows = conn.execute("SELECT name, embedding, sample_count FROM voiceprints").fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append({
                "name": r["name"],
                "emb": json.loads(r["embedding"]),
                "sample_count": r["sample_count"] or 1,
            })
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def get_voiceprints_by_name(name: str) -> List[Dict[str, Any]]:
    """某人的全部模板（含 id 与向量），给注册时"合并或新增"判断用。"""
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, embedding, sample_count FROM voiceprints WHERE name=?", (name.strip(),)
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            out.append({
                "id": r["id"],
                "emb": json.loads(r["embedding"]),
                "sample_count": r["sample_count"] or 1,
            })
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def add_voiceprint(name: str, emb: List[float], sample_count: int) -> None:
    """新增一份模板。"""
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute(
            """INSERT INTO voiceprints (id, name, embedding, sample_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), name.strip(), json.dumps(emb), int(sample_count), now, now),
        )
        conn.commit()


def update_voiceprint(vid: str, emb: List[float], sample_count: int) -> None:
    """更新某份模板（合并增强时用）。"""
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE voiceprints SET embedding=?, sample_count=?, updated_at=? WHERE id=?",
            (json.dumps(emb), int(sample_count), _now(), vid),
        )
        conn.commit()


def delete_voiceprints_by_name(name: str) -> int:
    """删除某人的全部模板，返回删除行数。"""
    with closing(get_conn()) as conn:
        cur = conn.execute("DELETE FROM voiceprints WHERE name=?", (name.strip(),))
        conn.commit()
        return cur.rowcount
