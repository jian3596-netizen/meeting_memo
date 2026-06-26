"""处理流水线编排（PRD 第 8 节状态机）。

uploaded → processing_audio → transcribing → cleaning_text → summarizing → completed
（DashScope 在 transcribing 一步内完成转写+分轨）。任一步失败 → failed，记录 failed_step。
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path
from typing import List, Optional

from collections import defaultdict

from . import audio, config, db, export
from .asr import FunASRLocal, cosine, get_asr, unload_model
from .llm import get_llm
from .models import MeetingSummary, Segment
from .textproc import apply_speaker_map, clean_segments, format_ts


# ----------------------------------------------------------------------------
# 串行处理队列：同一时刻只转一条，其余停在 "uploaded"（已上传，排队中）。
# 原因：FunASR 模型是进程内单例且无锁，CPU-only 并发既不安全也不会更快。
# ----------------------------------------------------------------------------
_task_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
_busy = False   # worker 是否正在处理一条会议（空闲卸载据此避免卸载在用的模型）

# 非终态（崩溃/重启后需要重新入队继续处理）
PENDING_STATES = {"uploaded", "processing_audio", "transcribing", "cleaning_text", "summarizing"}


def _worker_loop() -> None:
    global _busy
    while True:
        mid = _task_queue.get()
        _busy = True
        try:
            process_meeting(mid)
        except Exception:  # noqa: BLE001 单条失败不应让工作线程退出
            traceback.print_exc()
        finally:
            _busy = False
            _task_queue.task_done()


def _idle_unload_loop() -> None:
    """空闲超时后卸载 FunASR 模型释放内存（~3GB）。正在处理或队列有活时不卸载。

    下次上传/转写或点「初始化」会自动重新加载（模型已缓存，不再下载，只是重新载入内存）。
    """
    timeout = config.MODEL_IDLE_TIMEOUT
    check_every = min(60, max(5, timeout))
    while True:
        time.sleep(check_every)
        try:
            if _busy or not _task_queue.empty():
                continue
            if FunASRLocal._model is None:
                continue
            if time.monotonic() - (FunASRLocal._last_used or 0.0) >= timeout:
                unload_model()
        except Exception:  # noqa: BLE001 监控线程不因偶发异常退出
            traceback.print_exc()


def start_worker() -> None:
    """启动后台处理线程 +（可选）空闲卸载线程（幂等）。"""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        if config.MODEL_IDLE_TIMEOUT > 0:
            threading.Thread(target=_idle_unload_loop, daemon=True).start()
        _worker_started = True


def enqueue_meeting(mid: str) -> None:
    """把会议放入处理队列（FIFO，串行消费）。"""
    _task_queue.put(mid)


def requeue_pending() -> None:
    """重启后把所有未完成的会议重新排队，继续处理。"""
    for m in db.list_meetings():
        if m.get("status") in PENDING_STATES:
            enqueue_meeting(m["id"])


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


def rename_in_summary(mid: str, replacements: dict) -> None:
    """说话人改名后，把已生成的纪要 / 待办里出现的旧名替换为新名。

    replacements: {旧名(原始 SPEAKER_xx 或上一轮的显示名): 新名}。
    覆盖标题、摘要、讨论点、决策、待办负责人/事项、风险、未决问题，并重写 markdown。
    """
    replacements = {o: n for o, n in (replacements or {}).items() if o and n and o != n}
    if not replacements:
        return
    summary = load_summary(mid)
    if not summary:
        return

    def rep(s: Optional[str]) -> Optional[str]:
        if not s:
            return s
        for old, new in replacements.items():
            s = s.replace(old, new)
        return s

    summary.title = rep(summary.title)
    summary.summary = rep(summary.summary)
    for t in summary.topics:
        t.title, t.summary = rep(t.title), rep(t.summary)
    for d in summary.decisions:
        d.content = rep(d.content)
    for td in summary.todos:
        td.owner, td.task = rep(td.owner), rep(td.task)
    for r in summary.risks:
        r.content = rep(r.content)
    for q in summary.open_questions:
        q.content = rep(q.content)

    segments = load_segments(mid)
    _persist_summary(mid, summary, segments, config.LLM_MODEL)
    db.update_meeting(mid, title=summary.title)


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
        segments = get_asr().transcribe(
            processed, duration, hotword=hotword, spk_num=(meeting.get("spk_num") or None)
        )
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
        cat_name, cat_focus = _resolve_category(meeting.get("category"))
        summary = get_llm().summarize(segments, cat_name, cat_focus)
        from . import config
        _persist_summary(mid, summary, segments, config.LLM_MODEL)
        db.update_meeting(mid, title=summary.title)

        # 5. 完成
        db.set_status(mid, "completed", 100)

        # 转写成功后删除原始上传文件（界面回放只用 processed），省空间。
        # 仅在完成后删，失败的保留原文件以便重试/重排。
        try:
            orig = meeting.get("audio_path")
            if orig and orig != str(processed) and Path(orig).exists():
                Path(orig).unlink()
                db.update_meeting(mid, audio_path="")
        except Exception:  # noqa: BLE001 清理失败不应影响已完成的会议
            traceback.print_exc()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.mark_failed(mid, step, f"{type(e).__name__}: {e}")


def _resolve_category(name: Optional[str]):
    """会议分类 → (名称, 总结Prompt)；分类没设或没Prompt时回落到默认。"""
    from . import templates_prompts
    name = (name or "").strip()
    focus = db.get_category_prompt(name)
    if not focus:
        return (name or templates_prompts.DEFAULT_CATEGORY_NAME, templates_prompts.DEFAULT_CATEGORY_FOCUS)
    return (name, focus)


def regenerate(mid: str, category: Optional[str], custom_instruction: Optional[str]) -> None:
    """仅重跑总结步骤，复用已有转写（PRD 7.5）。category 为分类名（None=保持原分类）。"""
    meeting = db.get_meeting(mid)
    if not meeting:
        return
    if category is not None:
        db.update_meeting(mid, category=category)
        meeting["category"] = category
    try:
        db.set_status(mid, "summarizing", 80)
        segments = load_segments(mid)
        if not segments:
            raise RuntimeError("没有可用的转写，无法生成纪要")
        cat_name, cat_focus = _resolve_category(meeting.get("category"))
        summary = get_llm().summarize(segments, cat_name, cat_focus, custom_instruction)
        from . import config
        _persist_summary(mid, summary, segments, config.LLM_MODEL)
        db.update_meeting(mid, title=summary.title)
        db.set_status(mid, "completed", 100)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.mark_failed(mid, "summarizing", f"{type(e).__name__}: {e}")
