"""FastAPI Gateway（PRD 第 7 节 API）。

单机版：上传后用后台线程跑流水线，前端轮询状态。
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, export, pipeline
from .models import (
    CategoriesRequest,
    CreateMeetingResponse,
    HotwordsRequest,
    MeetingMetaRequest,
    MeetingSummary,
    RegenerateRequest,
    StatusResponse,
    VoiceprintEnrollRequest,
)

app = FastAPI(title="AI 会议纪要系统", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    pipeline.start_worker()       # 单线程串行处理队列
    pipeline.requeue_pending()    # 重启后继续处理未完成的会议


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _require(mid: str) -> Dict:
    m = db.get_meeting(mid)
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
    return m


def _jload(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default


def _meta_fields(m: Dict) -> Dict:
    """从 meeting 行解析出录音管理元数据；人员为空时回落到已识别的说话人。"""
    participants = _jload(m.get("participants"), [])
    if not participants:
        smap = _jload(m.get("speaker_map"), {})
        order: List[str] = []
        for v in (smap.values() if isinstance(smap, dict) else []):
            v = (v or "").strip()
            if v and v not in order:
                order.append(v)
        participants = order
    return {
        "category": m.get("category") or "",
        "tags": _jload(m.get("tags"), []),
        "participants": participants,
        "description": m.get("description") or "",
        "audio_time": m.get("audio_time") or "",
    }


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
    category: str = Form(""),
    spk_num: int = Form(0),
) -> CreateMeetingResponse:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的格式 {ext or '(无后缀)'}；支持：{sorted(config.ALLOWED_UPLOAD_EXTS)}",
        )

    title = Path(file.filename or "会议").stem
    mid = db.create_meeting(
        title=title, original_filename=file.filename or "", audio_path="",
        template_type="general",
    )
    dest = config.UPLOAD_DIR / f"{mid}{ext}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    fields = {"audio_path": str(dest)}
    if category.strip():
        fields["category"] = category.strip()   # 分类决定总结 Prompt
    if spk_num and spk_num > 0:
        fields["spk_num"] = spk_num   # 指定说话人数，避免长音频"少分"
    db.update_meeting(mid, **fields)

    pipeline.enqueue_meeting(mid)   # 入队，由单线程串行处理
    return CreateMeetingResponse(meeting_id=mid, status="uploaded")


# ---------------- 列表 / 详情 ----------------
@app.get("/api/hotwords")
def get_hotwords() -> Dict:
    return {"hotwords": db.get_hotwords()}


@app.put("/api/hotwords")
def update_hotwords(req: HotwordsRequest) -> Dict:
    cleaned = db.set_hotwords(req.hotwords)
    return {"ok": True, "hotwords": cleaned, "count": len(cleaned)}


# ---------------- 分类库（名称 + 总结 Prompt） ----------------
@app.get("/api/categories")
def get_categories() -> Dict:
    return {"categories": db.get_categories()}


@app.put("/api/categories")
def update_categories(req: CategoriesRequest) -> Dict:
    cleaned = db.set_categories([c.model_dump() for c in req.categories])
    return {"ok": True, "categories": cleaned}


# ---------------- 声纹（说话人自动识别） ----------------
@app.get("/api/voiceprints")
def get_voiceprints() -> Dict:
    return {"voiceprints": db.list_voiceprints()}


@app.delete("/api/voiceprints")
def remove_voiceprint(name: str) -> Dict:
    """删除某人的全部声纹模板（name 为查询参数）。"""
    n = db.delete_voiceprints_by_name(name)
    return {"ok": True, "deleted": n, "voiceprints": db.list_voiceprints()}


@app.post("/api/meetings/{mid}/voiceprints")
def enroll_voiceprint(mid: str, req: VoiceprintEnrollRequest) -> Dict:
    """从某会议的某说话人注册声纹（一人多模板）：

    聚合该说话人语音成一个声纹中心；若与此人已有某模板足够像（同设备）→ 合并增强，
    否则（换设备/新来源）→ 新增一份模板。匹配时取此人所有模板的最高分。
    """
    m = _require(mid)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")

    from .asr import cosine, get_asr, merge_centroids
    asr = get_asr()
    if not hasattr(asr, "embed_spans"):
        raise HTTPException(status_code=400, detail="当前 ASR 不支持声纹提取")

    wav = m.get("processed_path")
    if not wav or not Path(wav).exists():
        raise HTTPException(status_code=404, detail="音频文件不存在（会议可能未处理完）")

    spans = [
        (r["start_ms"] / 1000.0, r["end_ms"] / 1000.0)
        for r in db.get_segment_rows(mid)
        if r["speaker"] == req.speaker
    ]
    if not spans:
        raise HTTPException(status_code=400, detail=f"该会议中没有 {req.speaker} 的语音")

    emb = asr.embed_spans(wav, spans)
    if emb is None:
        raise HTTPException(status_code=400, detail="提取声纹失败（该说话人有效语音太短）")

    n_new = min(len(spans), config.VOICEPRINT_MAX_SEG)
    existing = db.get_voiceprints_by_name(name)
    best = None  # (score, template)
    for t in existing:
        s = cosine(emb, t["emb"])
        if best is None or s > best[0]:
            best = (s, t)
    if best and best[0] >= config.VOICEPRINT_MERGE_THRESHOLD:
        t = best[1]
        merged = merge_centroids(t["emb"], t["sample_count"], emb, n_new)
        db.update_voiceprint(t["id"], merged, t["sample_count"] + n_new)
        action = "merged"  # 同设备：增强已有模板
    else:
        db.add_voiceprint(name, emb, n_new)
        action = "added"   # 新设备/新来源：新增一份模板
    return {"ok": True, "name": name, "action": action, "voiceprints": db.list_voiceprints()}


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
            **_meta_fields(m),
        }
        for m in db.list_meetings()
    ]
    return {"meetings": items, "categories": db.get_categories()}


@app.patch("/api/meetings/{mid}/meta")
def update_meeting_meta(mid: str, req: MeetingMetaRequest) -> Dict:
    """更新录音管理元数据：标题 / 分类 / 标签 / 人员 / 描述。"""
    _require(mid)
    fields: Dict = {}
    if req.title is not None:
        t = req.title.strip()
        if t:
            fields["title"] = t
    if req.category is not None:
        fields["category"] = req.category.strip()
    if req.description is not None:
        fields["description"] = req.description.strip()
    if req.audio_time is not None:
        fields["audio_time"] = req.audio_time.strip()
    if req.tags is not None:
        clean = []
        for t in req.tags:
            t = (t or "").strip()
            if t and t not in clean:
                clean.append(t)
        fields["tags"] = json.dumps(clean, ensure_ascii=False)
    if req.participants is not None:
        clean = []
        for p in req.participants:
            p = (p or "").strip()
            if p and p not in clean:
                clean.append(p)
        fields["participants"] = json.dumps(clean, ensure_ascii=False)
    if fields:
        db.update_meeting(mid, **fields)
    m = db.get_meeting(mid) or {}
    return {"ok": True, "meeting": {"meeting_id": mid, "title": m.get("title"), **_meta_fields(m)}}


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
        **_meta_fields(m),
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
    _spawn(pipeline.regenerate, mid, req.category, req.custom_instruction)
    return CreateMeetingResponse(meeting_id=mid, status="summarizing")


# ---------------- 说话人改名（7.6） ----------------
@app.post("/api/meetings/{mid}/speakers")
def update_speakers(mid: str, mapping: Dict[str, str]) -> Dict:
    _require(mid)
    prev = db.get_speaker_map(mid)
    db.set_speaker_map(mid, mapping)
    # 改名后，把纪要/待办里的旧名（原始 SPEAKER_xx 或上一轮的显示名）替换为新名
    replacements: Dict[str, str] = {}
    for spk, name in mapping.items():
        name = (name or "").strip()
        if not name:
            continue
        old_disp = (prev.get(spk) or "").strip()
        if old_disp and old_disp != name:
            replacements[old_disp] = name
        if spk != name:
            replacements.setdefault(spk, name)
    try:
        pipeline.rename_in_summary(mid, replacements)
    except Exception:  # noqa: BLE001 改名是辅助操作，纪要重写失败不应让改名整体失败
        pass
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
