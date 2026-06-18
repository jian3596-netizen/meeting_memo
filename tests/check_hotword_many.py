"""验证 FunASR 能否吃下 ~300 个热词，以及对速度的影响。"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
os.environ["ASR_PROVIDER"] = "funasr"

from app import audio, config
from app.asr import FunASRLocal

REAL = Path("E:/Git/meeting_memo/temp/20230515 注册问题（欧阳老师）.m4a")

# 真实相关词 + 凑数到 300
REAL_WORDS = ["协和医院", "杜斌", "瑞芯微", "答辩", "免临床", "创新医疗器械",
              "全血检测", "血浆检测", "检验科", "血液科", "免疫抑制剂", "给药指南"]
HOTWORDS = REAL_WORDS + [f"专业术语{i:03d}" for i in range(300 - len(REAL_WORDS))]


def main() -> int:
    clip = config.DATA_DIR / "hotword_clip_60.wav"
    if not clip.exists():
        subprocess.run(
            [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(REAL),
             "-t", "60", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)],
            check=True,
        )
    dur = audio.wav_duration_sec(clip)
    asr = FunASRLocal()
    print(f"热词数={len(HOTWORDS)}, clip={dur:.0f}s", flush=True)

    t = time.time()
    segs0 = asr.transcribe(clip, dur, hotword="")
    base = time.time() - t
    print(f"无热词:   {len(segs0)}句, {base:.0f}s", flush=True)

    t = time.time()
    segs = asr.transcribe(clip, dur, hotword=" ".join(HOTWORDS))
    many = time.time() - t
    print(f"300热词: {len(segs)}句, {many:.0f}s  (慢 {many-base:+.0f}s)", flush=True)
    print("OK ✅ 300 热词被接受，无报错", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
