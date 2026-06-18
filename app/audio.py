"""音频预处理（PRD 3.3）。

用 imageio-ffmpeg 自带的 ffmpeg 二进制，免去用户手动安装；统一转 16kHz 单声道 wav，
视频文件自动抽音轨。说话人分离要求单声道且 ≤2h，超长自动切块。
"""

from __future__ import annotations

import contextlib
import subprocess
import wave
from pathlib import Path
from typing import List, Tuple

import imageio_ffmpeg

from . import config


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: List[str]) -> None:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr.strip() or proc.stdout.strip()}")


def to_wav(src: Path, dst: Path) -> None:
    """转 16kHz 单声道 wav（-vn 丢弃视频轨，只保留音频）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "-i", str(src),
        "-vn",
        "-ac", str(config.TARGET_CHANNELS),
        "-ar", str(config.TARGET_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(dst),
    ])


def wav_duration_sec(wav_path: Path) -> float:
    with contextlib.closing(wave.open(str(wav_path), "rb")) as w:
        frames = w.getnframes()
        rate = w.getframerate() or config.TARGET_SAMPLE_RATE
        return frames / float(rate)


def split_wav(wav_path: Path, chunk_sec: int) -> List[Tuple[Path, float]]:
    """把长 wav 切成 ≤chunk_sec 的片段，返回 [(片段路径, 起始偏移秒)]。"""
    duration = wav_duration_sec(wav_path)
    if duration <= chunk_sec:
        return [(wav_path, 0.0)]

    out: List[Tuple[Path, float]] = []
    offset = 0.0
    i = 0
    while offset < duration:
        part = wav_path.with_name(f"{wav_path.stem}_part{i:03d}.wav")
        _run([
            "-i", str(wav_path),
            "-ss", str(offset),
            "-t", str(chunk_sec),
            "-ac", str(config.TARGET_CHANNELS),
            "-ar", str(config.TARGET_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            str(part),
        ])
        out.append((part, offset))
        offset += chunk_sec
        i += 1
    return out


def prepare(src: Path, mid: str) -> Tuple[Path, float]:
    """转码并返回 (processed_wav_path, duration_sec)。"""
    dst = config.PROCESSED_DIR / f"{mid}.wav"
    to_wav(src, dst)
    return dst, wav_duration_sec(dst)
