"""本地 ASR + 说话人分离（PRD 3.4 / 3.5）。

始终用本地 FunASR（Paraformer + fsmn-vad + ct-punc + cam++）离线转写，音频不出本机。
运行设备由 config.funasr_device() 决定：检测到 GPU 自动走 GPU，否则 CPU。
顺带复用 cam++ 抽声纹（embed_spans），供说话人自动识别。
"""

from __future__ import annotations

import gc
import threading
import time
import traceback
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
    _last_used = 0.0  # 最近一次使用的 time.monotonic()，给空闲卸载判断用
    _lock = threading.Lock()  # 串行化模型构建：处理线程与「初始化」预热并发时也只加载一次

    def __init__(self) -> None:
        from funasr import AutoModel
        if FunASRLocal._model is None:
            with FunASRLocal._lock:
                if FunASRLocal._model is None:   # 双重检查：拿到锁后再确认一次
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
        FunASRLocal._last_used = time.monotonic()

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
        FunASRLocal._last_used = time.monotonic()
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


# ---- 模型预热（让用户在第一场会议前先把模型下载/加载好）----
_warmup_lock = threading.Lock()
_warmup_status = "idle"     # idle | loading | ready | failed
_warmup_message = ""


def warmup_state() -> dict:
    """当前模型预热状态。无论由谁触发，只要模型已在内存即视为 ready。"""
    global _warmup_status
    if FunASRLocal._model is not None and _warmup_status != "ready":
        _warmup_status = "ready"
    return {
        "status": _warmup_status,
        "message": _warmup_message,
        "device": FunASRLocal._device,
        "loaded": FunASRLocal._model is not None,
    }


def _warmup_run() -> None:
    global _warmup_status, _warmup_message
    try:
        FunASRLocal()  # 构建即加载（线程安全，内部有锁）
        _warmup_status, _warmup_message = "ready", ""
        print("[warmup] 模型就绪", flush=True)
    except Exception as e:  # noqa: BLE001 预热失败仅置状态；真正处理会议时还会再报一次
        _warmup_status, _warmup_message = "failed", f"{type(e).__name__}: {e}"
        traceback.print_exc()


def trigger_warmup() -> dict:
    """非阻塞触发模型预热（幂等）：起后台线程加载，前端轮询 warmup_state 即可。"""
    global _warmup_status, _warmup_message
    with _warmup_lock:
        if FunASRLocal._model is not None:
            _warmup_status = "ready"
        elif _warmup_status != "loading":
            _warmup_status, _warmup_message = "loading", "正在加载/下载模型…"
            threading.Thread(target=_warmup_run, daemon=True).start()
    return warmup_state()


def _malloc_trim() -> None:
    """Linux glibc：把空闲堆内存还给 OS，让 RSS 真正下降；非 glibc 平台静默跳过。"""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass


def unload_model() -> bool:
    """卸载常驻的 FunASR 模型，释放内存（~3GB）。返回是否真的卸载了。

    与构建共用 _lock，确保不会和正在进行的加载/转写抢同一份模型；
    卸载后预热状态归零，前端会重新显示「初始化模型」。
    """
    with FunASRLocal._lock:
        if FunASRLocal._model is None:
            return False
        FunASRLocal._model = None
        FunASRLocal._device = None
    global _warmup_status, _warmup_message
    with _warmup_lock:
        _warmup_status, _warmup_message = "idle", ""
    gc.collect()
    _malloc_trim()
    print("[funasr] 模型已卸载（空闲释放内存）", flush=True)
    return True


def get_asr():
    """ASR 始终为本地 FunASR（离线、音频不出本机）。"""
    return FunASRLocal()


# ---- 子进程隔离：每场会议在独立进程跑模型，跑完即退、内存全部还给 OS ----
def _run_worker(req: dict) -> dict:
    """启子进程 `python -m app.asr_worker` 处理一次请求；进程退出后内存被 OS 回收。"""
    import json
    import os
    import subprocess
    import sys
    import tempfile

    d = tempfile.mkdtemp(prefix="asrjob_")
    reqp = os.path.join(d, "req.json")
    outp = os.path.join(d, "out.json")
    try:
        with open(reqp, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, "-m", "app.asr_worker", reqp, outp],
            capture_output=True, text=True,
        )
        if not os.path.exists(outp):
            tail = (proc.stderr or proc.stdout or "")[-1000:]
            raise RuntimeError(f"ASR 子进程失败 (code={proc.returncode}): {tail}")
        with open(outp, encoding="utf-8") as f:
            return json.load(f)
    finally:
        for p in (reqp, outp):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def transcribe_job(wav, duration: float, hotword: str = "", spk_num: Optional[int] = None,
                   want_embeddings: bool = False):
    """转写一场会议，返回 (segments, {speaker: 声纹中心})。

    config.ASR_IN_SUBPROCESS=1（默认）走子进程；否则进程内常驻模型。
    want_embeddings=True 时顺带在同一进程里抽各 SPEAKER 的声纹中心（供自动命名），避免再加载模型。
    """
    if config.ASR_IN_SUBPROCESS:
        res = _run_worker({
            "op": "transcribe", "wav": str(wav), "duration": duration,
            "hotword": hotword, "spk_num": spk_num, "want_embeddings": want_embeddings,
        })
        segs = [Segment(**d) for d in res.get("segments", [])]
        return segs, (res.get("embeddings") or {})

    asr = FunASRLocal()
    segs = asr.transcribe(Path(wav), duration, hotword=hotword, spk_num=spk_num)
    emb = {}
    if want_embeddings:
        from collections import defaultdict
        spans = defaultdict(list)
        for s in segs:
            spans[s.speaker].append((s.start_seconds, s.end_seconds))
        for spk, sp in spans.items():
            v = asr.embed_spans(Path(wav), sp)
            if v is not None:
                emb[spk] = v
    return segs, emb


def embed_job(wav, spans) -> Optional[List[float]]:
    """对若干语音段抽声纹中心（存声纹时用）。子进程模式下独立进程跑，跑完即退。"""
    if config.ASR_IN_SUBPROCESS:
        res = _run_worker({"op": "embed", "wav": str(wav), "spans": [list(s) for s in spans]})
        return res.get("embedding")
    return FunASRLocal().embed_spans(Path(wav), spans)
