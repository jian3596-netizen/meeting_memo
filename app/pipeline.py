"""处理流水线编排（PRD 第 8 节状态机）。

uploaded → processing_audio → transcribing → cleaning_text → summarizing → completed
（DashScope 在 transcribing 一步内完成转写+分轨）。任一步失败 → failed，记录 failed_step。
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import List, Optional

from collections import defaultdict

from . import audio, config, db, export
from .asr import cosine, get_asr
from .llm import get_llm
from .models import MeetingSummary, Segment
from .textproc import apply_speaker_map, clean_segments, format_ts


def _auto_match_speakers(mid: str, wav: Path, segments: List[Segment]) -> None:
    """分轨后按声纹自动给说话人命名（best-effort）。

    - 仅 funasr（asr 具备 embed_spans）且已注册过声纹时生效。
    - 每个 SPEAKER_xx 聚合声纹中心 → 与已注册者算余弦 → 超阈值且 Top1-Top2 差够大才认。
    - 不覆盖已有的手动改名；同一个名字不会同时分给两个说话人（高分优先）。
    """
    if not config.VOICEPRINT_ENABLED:
        return
    asr = get_asr()
    if not hasattr(asr, "embed_spans"):
        return
    enrolled = db.get_voiceprints()
    if not enrolled:
        return
    # 按人聚合所有模板（一人多份，匹配取最高分）
    templates = defaultdict(list)
    for e in enrolled:
        templates[e["name"]].append(e["emb"])

    spans = defaultdict(list)
    for s in segments:
        spans[s.speaker].append((s.start_seconds, s.end_seconds))

    candidates = []  # (score, speaker, name)
    for spk, sp in spans.items():
        centroid = asr.embed_spans(wav, sp)
        if centroid is None:
            continue
        # 每个人取其所有模板中的最高余弦
        sims = sorted(
            ((max(cosine(centroid, t) for t in tlist), name) for name, tlist in templates.items()),
            reverse=True,
        )
        best_score, best_name = sims[0]
        second = sims[1][0] if len(sims) > 1 else 0.0
        if best_score >= config.VOICEPRINT_THRESHOLD and (best_score - second) >= config.VOICEPRINT_MARGIN:
            candidates.append((best_score, spk, best_name))

    if not candidates:
        return
    existing = db.get_speaker_map(mid)
    candidates.sort(reverse=True)
    assigned, used_names = {}, set()
    for score, spk, name in candidates:
        if spk in existing or spk in assigned or name in used_names:
            continue
        assigned[spk] = name
        used_names.add(name)
    if assigned:
        merged = dict(existing)
        merged.update(assigned)
        db.set_speaker_map(mid, merged)
        print(f"[voiceprint] 自动命名: {assigned}", flush=True)


def load_segments(mid: str) -> List[Segment]:
    """从 DB 读回片段并套用说话人改名。"""
    smap = db.get_speaker_map(mid)
    segs: List[Segment] = []
    for r in db.get_segment_rows(mid):
        start_s = (r["start_ms"] or 0) / 1000.0
        end_s = (r["end_ms"] or 0) / 1000.0
        segs.append(Segment(
            idx=r["idx"],
            speaker=r["speaker"] or "SPEAKER_00",
            start=format_ts(start_s), end=format_ts(end_s),
            start_seconds=start_s, end_seconds=end_s,
            text=r["clean_text"] or r["raw_text"] or "",
            raw_text=r["raw_text"] or "",
        ))
    apply_speaker_map(segs, smap)
    return segs


def load_summary(mid: str) -> Optional[MeetingSummary]:
    row = db.get_summary_row(mid)
    if not row:
        return None
    return MeetingSummary.model_validate_json(row["summary_json"])


def _persist_summary(mid: str, summary: MeetingSummary, segments: List[Segment], model_name: str) -> None:
    meeting = db.get_meeting(mid) or {}
    markdown = export.to_markdown(meeting, summary, segments)
    db.save_summary(mid, summary, markdown, model_name)
    db.save_tasks(mid, summary)


def process_meeting(mid: str) -> None:
    """后台线程入口：跑完整条链路。"""
    meeting = db.get_meeting(mid)
    if not meeting:
        return
    step = "init"
    try:
        # 1. 音频预处理
        step = "processing_audio"
        db.set_status(mid, "processing_audio", 10)
        processed, duration = audio.prepare(Path(meeting["audio_path"]), mid)
        db.update_meeting(mid, processed_path=str(processed), duration_sec=duration)

        # 2. 转写 + 说话人分离
        step = "transcribing"
        db.set_status(mid, "transcribing", 40)
        hotword = " ".join(db.get_hotwords())
        segments = get_asr().transcribe(processed, duration, hotword=hotword)
        if not segments:
            raise RuntimeError("ASR 未返回任何文本（音频可能为空或无人声）")

        # 3. 文本清洗
        step = "cleaning_text"
        db.set_status(mid, "cleaning_text", 65)
        clean_segments(segments)
        db.save_segments(mid, segments)

        # 3.5 声纹自动命名（best-effort，失败不影响主流程）
        try:
            _auto_match_speakers(mid, processed, segments)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        # 4. 结构化纪要
        step = "summarizing"
        db.set_status(mid, "summarizing", 80)
        apply_speaker_map(segments, db.get_speaker_map(mid))
        summary = get_llm().summarize(segments, meeting.get("template_type", "general"))
        from . import config
        _persist_summary(mid, summary, segments, config.LLM_MODEL)
        db.update_meeting(mid, title=summary.title)

        # 5. 完成
        db.set_status(mid, "completed", 100)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.mark_failed(mid, step, f"{type(e).__name__}: {e}")


def regenerate(mid: str, template_type: Optional[str], custom_instruction: Optional[str]) -> None:
    """仅重跑总结步骤，复用已有转写（PRD 7.5）。"""
    meeting = db.get_meeting(mid)
    if not meeting:
        return
    if template_type:
        db.update_meeting(mid, template_type=template_type)
        meeting["template_type"] = template_type
    try:
        db.set_status(mid, "summarizing", 80)
        segments = load_segments(mid)
        if not segments:
            raise RuntimeError("没有可用的转写，无法生成纪要")
        summary = get_llm().summarize(
            segments, meeting.get("template_type", "general"), custom_instruction
        )
        from . import config
        _persist_summary(mid, summary, segments, config.LLM_MODEL)
        db.update_meeting(mid, title=summary.title)
        db.set_status(mid, "completed", 100)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.mark_failed(mid, "summarizing", f"{type(e).__name__}: {e}")
