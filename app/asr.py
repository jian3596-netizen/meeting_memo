"""ASR + 说话人分离（PRD 3.4 / 3.5）。

DashScope Paraformer 录音文件识别（异步任务）：
  提交 file_urls(本地用 file:// 绝对路径) → 轮询 → 下载结果 JSON → 解析带 speaker_id 的句子。
说话人分离要求单声道 ≤2h，超长由 audio.split_wav 切块，逐块识别后按偏移拼接。

Fake provider：返回固定的中文会议片段，便于无网络/无 key 时跑通整条链路。
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any, List

import requests

from . import audio, config
from .models import Segment
from .textproc import format_ts


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """dashscope 返回有时是 dict、有时是对象，统一取值。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _fmt_speaker(spk: Any) -> str:
    try:
        return f"SPEAKER_{int(spk):02d}"
    except (TypeError, ValueError):
        return "SPEAKER_00" if spk in (None, "") else f"SPEAKER_{spk}"


def _parse_paraformer_json(data: dict, offset_sec: float, start_idx: int) -> List[Segment]:
    segments: List[Segment] = []
    transcripts = data.get("transcripts") or []
    idx = start_idx
    for tr in transcripts:
        for sent in tr.get("sentences") or []:
            begin_ms = sent.get("begin_time", 0) or 0
            end_ms = sent.get("end_time", begin_ms) or begin_ms
            start_s = offset_sec + begin_ms / 1000.0
            end_s = offset_sec + end_ms / 1000.0
            text = (sent.get("text") or "").strip()
            if not text:
                continue
            segments.append(Segment(
                idx=idx,
                speaker=_fmt_speaker(sent.get("speaker_id")),
                start=format_ts(start_s),
                end=format_ts(end_s),
                start_seconds=round(start_s, 3),
                end_seconds=round(end_s, 3),
                text=text,
                raw_text=text,
            ))
            idx += 1
    return segments


class DashScopeASR:
    def __init__(self) -> None:
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法调用 DashScope ASR")
        import dashscope
        dashscope.api_key = config.DASHSCOPE_API_KEY
        from dashscope.audio.asr import Transcription
        self._Transcription = Transcription

    def _transcribe_chunk(self, wav: Path, offset_sec: float, start_idx: int) -> List[Segment]:
        uri = wav.resolve().as_uri()  # file:///E:/...
        task = self._Transcription.async_call(
            model=config.ASR_MODEL,
            file_urls=[uri],
            language_hints=["zh", "en"],
            diarization_enabled=True,
        )
        task_id = _get(_get(task, "output"), "task_id")
        if not task_id:
            raise RuntimeError(f"ASR 任务提交失败: {_get(task, 'message') or task}")

        result = self._Transcription.wait(task=task_id)
        if _get(result, "status_code") not in (HTTPStatus.OK, 200):
            raise RuntimeError(f"ASR 任务失败: {_get(result, 'message')}")

        output = _get(result, "output")
        if _get(output, "task_status") != "SUCCEEDED":
            raise RuntimeError(f"ASR 未成功: {_get(output, 'task_status')} {output}")

        segments: List[Segment] = []
        idx = start_idx
        for item in _get(output, "results") or []:
            if _get(item, "subtask_status") not in ("SUCCEEDED", None):
                continue
            url = _get(item, "transcription_url")
            if not url:
                continue
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            # 留档
            (config.RESULT_DIR / f"{wav.stem}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            chunk_segs = _parse_paraformer_json(data, offset_sec, idx)
            segments.extend(chunk_segs)
            idx += len(chunk_segs)
        return segments

    def transcribe(self, wav: Path, duration_sec: float, hotword: str = "") -> List[Segment]:
        chunks = audio.split_wav(wav, config.DIARIZATION_MAX_SECONDS)
        segments: List[Segment] = []
        for part, offset in chunks:
            segments.extend(self._transcribe_chunk(part, offset, len(segments)))
        return segments


class FakeASR:
    """无网络/无 key 时的假数据，模拟一场 3 人技术评审。"""

    def transcribe(self, wav: Path, duration_sec: float, hotword: str = "") -> List[Segment]:
        script = [
            (3, 9, 0, "我们今天主要讨论一下前后端接口的设计问题，时间有限我们快速过一下。"),
            (10, 18, 1, "我建议先把通讯格式固定下来，这样前端和后端可以并行开发，互不阻塞。"),
            (19, 27, 2, "同意，不过我担心接口过早固定，后面多 Agent 能力扩展的时候会受限。"),
            (28, 38, 0, "那这样，先定义统一的 API v0.1，再分别实现本地模型、云模型和多 Agent 后端。"),
            (39, 47, 1, "好的，那接口草案我这周五之前整理出来发群里。"),
            (48, 58, 2, "本地 ASR 到底是部署在板子上还是先调云端，这个还得再确认一下。"),
        ]
        segs: List[Segment] = []
        for i, (st, en, spk, text) in enumerate(script):
            segs.append(Segment(
                idx=i,
                speaker=f"SPEAKER_{spk:02d}",
                start=format_ts(st), end=format_ts(en),
                start_seconds=float(st), end_seconds=float(en),
                text=text, raw_text=text,
            ))
        return segs


class FunASRLocal:
    """本地离线 ASR + 说话人分轨（FunASR / Paraformer + cam++）。

    音频完全不出本机。模型首次运行从 ModelScope 自动下载（~1GB），之后缓存。
    模型加载较重，进程内用类级缓存复用。
    """

    _model = None  # 进程内复用，避免每次重载

    def __init__(self) -> None:
        from funasr import AutoModel
        if FunASRLocal._model is None:
            FunASRLocal._model = AutoModel(
                model=config.FUNASR_ASR_MODEL,
                vad_model=config.FUNASR_VAD_MODEL,
                punc_model=config.FUNASR_PUNC_MODEL,
                spk_model=config.FUNASR_SPK_MODEL,
                disable_update=True,
            )
        self.model = FunASRLocal._model

    def transcribe(self, wav: Path, duration_sec: float, hotword: str = "") -> List[Segment]:
        kw = {"batch_size_s": 300}
        spk_num = config.funasr_spk_num()
        if spk_num:
            kw["preset_spk_num"] = spk_num  # 强制聚类成指定人数，长音频更稳
        if hotword:
            kw["hotword"] = hotword  # 热词偏置（空格分隔；需支持热词的模型，见 config）
        res = self.model.generate(input=str(wav), **kw)
        if not res:
            return []
        info = res[0].get("sentence_info") or []
        segments: List[Segment] = []
        idx = 0
        for s in info:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            start_s = (s.get("start", 0) or 0) / 1000.0
            end_s = (s.get("end", start_s * 1000) or 0) / 1000.0
            segments.append(Segment(
                idx=idx,
                speaker=_fmt_speaker(s.get("spk", 0)),
                start=format_ts(start_s), end=format_ts(end_s),
                start_seconds=round(start_s, 3), end_seconds=round(end_s, 3),
                text=text, raw_text=text,
            ))
            idx += 1
        return segments


def get_asr():
    if config.asr_is_fake():
        return FakeASR()
    if config.ASR_PROVIDER == "funasr":
        return FunASRLocal()
    return DashScopeASR()
