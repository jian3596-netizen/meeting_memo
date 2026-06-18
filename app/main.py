"""FastAPI Gateway（PRD 第 7 节 API）。

单机版：上传后用后台线程跑流水线，前端轮询状态。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, export, pipeline
from .models import (
    CreateMeetingResponse,
    HotwordsRequest,
    MeetingSummary,
    RegenerateRequest,
    StatusResponse,
)
from .templates_prompts import TEMPLATES

app = FastAPI(title="AI 会议纪要系统", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _require(mid: str) -> Dict:
    m = db.get_meeting(mid)
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
    return m


def _content_disposition(filename: str) -> str:
    """RFC 5987：HTTP 头只能 latin-1，中文文件名需用 filename* 编码。"""
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "meeting"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


# ---------------- 页面 ----------------
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((config.WEB_DIR / "index.html").read_text(encoding="utf-8"))


# ---------------- 上传（7.1） ----------------
@app.post("/api/meetings", response_model=CreateMeetingResponse)
async def create_meeting(
    file: UploadFile = File(...),
    template_type: str = Form("general"),
) -> CreateMeetingResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的格式 {ext or '(无后缀)'}；支持：{sorted(config.ALLOWED_UPLOAD_EXTS)}",
        )
    if template_type not in TEMPLATES:
        template_type = "general"

    title = Path(file.filename or "会议").stem
    mid = db.create_meeting(
        title=title, original_filename=file.filename or "", audio_path="",
        template_type=template_type,
    )
    dest = config.UPLOAD_DIR / f"{mid}{ext}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    db.update_meeting(mid, audio_path=str(dest))

    _spawn(pipeline.process_meeting, mid)
    return CreateMeetingResponse(meeting_id=mid, status="uploaded")


# ---------------- 列表 / 详情 ----------------
@app.get("/api/hotwords")
def get_hotwords() -> Dict:
    return {"hotwords": db.get_hotwords()}


@app.put("/api/hotwords")
def update_hotwords(req: HotwordsRequest) -> Dict:
    cleaned = db.set_hotwords(req.hotwords)
    return {"ok": True, "hotwords": cleaned, "count": len(cleaned)}


@app.get("/api/meetings")
def list_meetings() -> Dict:
    items = [
        {
            "meeting_id": m["id"],
            "title": m["title"],
            "status": m["status"],
            "progress": m["progress"],
            "template_type": m["template_type"],
            "duration_sec": m["duration_sec"],
            "created_at": m["created_at"],
        }
        for m in db.list_meetings()
    ]
    return {"meetings": items, "templates": TEMPLATES}


@app.get("/api/meetings/{mid}")
def get_meeting(mid: str) -> Dict:
    m = _require(mid)
    return {
        "meeting_id": m["id"],
        "title": m["title"],
        "status": m["status"],
        "progress": m["progress"],
        "template_type": m["template_type"],
        "duration_sec": m["duration_sec"],
        "original_filename": m["original_filename"],
        "failed_step": m["failed_step"],
        "error_message": m["error_message"],
        "speaker_map": db.get_speaker_map(mid),
    }


# ---------------- 状态（7.2） ----------------
@app.get("/api/meetings/{mid}/status", response_model=StatusResponse)
def get_status(mid: str) -> StatusResponse:
    m = _require(mid)
    return StatusResponse(
        meeting_id=mid, status=m["status"], progress=m["progress"],
        failed_step=m["failed_step"], error_message=m["error_message"],
    )


# ---------------- 转写全文（7.3） ----------------
@app.get("/api/meetings/{mid}/transcript")
def get_transcript(mid: str) -> Dict:
    _require(mid)
    segs = pipeline.load_segments(mid)
    return {
        "segments": [
            {
                "idx": s.idx,
                "speaker": s.speaker,
                "speaker_name": s.speaker_name,
                "display": s.speaker_name or s.speaker,
                "start": s.start,
                "end": s.end,
                "start_seconds": s.start_seconds,
                "text": s.text,
                "raw_text": s.raw_text,
            }
            for s in segs
        ]
    }


# ---------------- 纪要（7.4 / 编辑保存） ----------------
@app.get("/api/meetings/{mid}/summary")
def get_summary(mid: str) -> Dict:
    _require(mid)
    summary = pipeline.load_summary(mid)
    if not summary:
        raise HTTPException(status_code=404, detail="纪要尚未生成")
    return summary.model_dump()


@app.put("/api/meetings/{mid}/summary")
def update_summary(mid: str, summary: MeetingSummary) -> Dict:
    _require(mid)
    segs = pipeline.load_segments(mid)
    meeting = db.get_meeting(mid) or {}
    markdown = export.to_markdown(meeting, summary, segs)
    db.save_summary(mid, summary, markdown, config.LLM_MODEL)
    db.save_tasks(mid, summary)
    db.update_meeting(mid, title=summary.title)
    return {"ok": True}


# ---------------- 重新生成（7.5） ----------------
@app.post("/api/meetings/{mid}/regenerate", response_model=CreateMeetingResponse)
def regenerate(mid: str, req: RegenerateRequest) -> CreateMeetingResponse:
    _require(mid)
    _spawn(pipeline.regenerate, mid, req.template_type, req.custom_instruction)
    return CreateMeetingResponse(meeting_id=mid, status="summarizing")


# ---------------- 说话人改名（7.6） ----------------
@app.post("/api/meetings/{mid}/speakers")
def update_speakers(mid: str, mapping: Dict[str, str]) -> Dict:
    _require(mid)
    db.set_speaker_map(mid, mapping)
    return {"ok": True, "speaker_map": mapping}


# ---------------- 待办 ----------------
@app.get("/api/meetings/{mid}/tasks")
def get_tasks(mid: str) -> Dict:
    _require(mid)
    return {"tasks": db.get_task_rows(mid)}


# ---------------- 音频回放（时间戳追溯） ----------------
@app.get("/api/meetings/{mid}/audio")
def get_audio(mid: str):
    m = _require(mid)
    path = m.get("processed_path") or m.get("audio_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(path, media_type="audio/wav", filename=Path(path).name)


# ---------------- 导出（7.7） ----------------
@app.get("/api/meetings/{mid}/export")
def export_meeting(mid: str, format: str = "md"):
    m = _require(mid)
    summary = pipeline.load_summary(mid)
    if not summary:
        raise HTTPException(status_code=404, detail="纪要尚未生成")
    segs = pipeline.load_segments(mid)
    base = (m.get("title") or "meeting").replace("/", "_").replace("\\", "_")

    if format == "md":
        content = export.to_markdown(m, summary, segs)
        return Response(
            content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(f"{base}.md")},
        )
    if format == "docx":
        data = export.to_docx(m, summary, segs)
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": _content_disposition(f"{base}.docx")},
        )
    raise HTTPException(status_code=400, detail="format 仅支持 md / docx（pdf 暂未实现）")


# ---------------- 删除 ----------------
@app.delete("/api/meetings/{mid}")
def delete_meeting(mid: str) -> Dict:
    m = _require(mid)
    for key in ("audio_path", "processed_path"):
        p = m.get(key)
        if p and Path(p).exists():
            try:
                Path(p).unlink()
            except OSError:
                pass
    db.delete_meeting(mid)
    return {"ok": True}


# 静态资源（前端 JS/CSS）
app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
