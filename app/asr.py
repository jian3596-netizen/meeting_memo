"""本地 ASR + 说话人分离（PRD 3.4 / 3.5）。

始终用本地 FunASR（Paraformer + fsmn-vad + ct-punc + cam++）离线转写，音频不出本机。
运行设备由 config.funasr_device() 决定：检测到 GPU 自动走 GPU，否则 CPU。
顺带复用 cam++ 抽声纹（embed_spans），供说话人自动识别。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import numpy as np

from . import config
from .models import Segment
from .textproc import format_ts


# ---- 声纹相似度工具（余弦 / 加权合并），cam++ 内部也是先 L2 归一化再点积 ----
def cosine(a, b) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8))


def merge_centroids(a, na: int, b, nb: int) -> List[float]:
    """按样本数加权合并两个声纹中心，再归一化（重复注册时增强声纹）。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = a * float(na) + b * float(nb)
    m = m / (np.linalg.norm(m) + 1e-8)
    return [float(x) for x in m]


def _fmt_speaker(spk: Any) -> str:
    try:
        return f"SPEAKER_{int(spk):02d}"
    except (TypeError, ValueError):
        return "SPEAKER_00" if spk in (None, "") else f"SPEAKER_{spk}"


class FunASRLocal:
    """本地离线 ASR + 说话人分轨（FunASR / Paraformer + cam++）。

    音频完全不出本机。模型首次运行从 ModelScope 自动下载（~1GB），之后缓存。
    模型加载较重，进程内用类级缓存复用；设备（GPU/CPU）由 config.funasr_device() 决定。
    """

    _model = None     # 进程内复用，避免每次重载
    _device = None    # 与 _model 对应的设备（cuda / cuda:0 / cpu）

    def __init__(self) -> None:
        from funasr import AutoModel
        if FunASRLocal._model is None:
            device = config.funasr_device()
            FunASRLocal._model = AutoModel(
                model=config.FUNASR_ASR_MODEL,
                vad_model=config.FUNASR_VAD_MODEL,
                punc_model=config.FUNASR_PUNC_MODEL,
                spk_model=config.FUNASR_SPK_MODEL,
                device=device,            # cuda / cuda:0 / cpu，GPU 不可用时由 config 回落 cpu
                disable_update=True,
            )
            FunASRLocal._device = device
            print(f"[funasr] 模型已加载，device={device}", flush=True)
        self.model = FunASRLocal._model
        self.device = FunASRLocal._device

    def transcribe(self, wav: Path, duration_sec: float, hotword: str = "", spk_num: "int | None" = None) -> List[Segment]:
        kw = {"batch_size_s": 300}
        n = spk_num if spk_num else config.funasr_spk_num()  # 每会议指定优先，否则用全局默认
        if n:
            kw["preset_spk_num"] = n  # 强制聚类成指定人数，长音频更稳
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

    def embed_spans(self, wav: Path, spans, max_segments: Optional[int] = None,
                    min_dur: float = 1.0) -> Optional[List[float]]:
        """对若干 (start_s, end_s) 语音段抽 cam++ 声纹，聚合成一个 L2 归一化中心向量（192维）。

        复用已加载的 self.model.spk_model（CAMPPlus），不另加载模型。
        取最长的若干段以提高稳健性；全部太短则返回 None。
        """
        import wave

        if max_segments is None:
            max_segments = config.VOICEPRINT_MAX_SEG
        spans = [(float(s), float(e)) for s, e in spans if (e - s) >= min_dur]
        spans.sort(key=lambda se: se[1] - se[0], reverse=True)
        spans = spans[:max_segments]
        if not spans:
            return None

        spk_model = self.model.spk_model
        device = self.device
        vecs: List[np.ndarray] = []
        with wave.open(str(wav), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            if w.getsampwidth() != 2:          # 仅支持 16-bit PCM（audio.prepare 产物）
                return None
            for st, en in spans:
                w.setpos(max(0, int(st * sr)))
                raw = w.readframes(int((en - st) * sr))
                x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if ch > 1:
                    x = x.reshape(-1, ch).mean(axis=1)
                if len(x) < int(0.5 * sr):
                    continue
                out, _ = spk_model.inference(data_in=[x], key=["x"], device=device, fs=16000)
                v = out[0]["spk_embedding"].detach().cpu().numpy().reshape(-1)
                vecs.append(v / (np.linalg.norm(v) + 1e-8))
        if not vecs:
            return None
        c = np.mean(vecs, axis=0)
        c = c / (np.linalg.norm(c) + 1e-8)
        return [float(x) for x in c]


def get_asr():
    """ASR 始终为本地 FunASR（离线、音频不出本机）。"""
    return FunASRLocal()
