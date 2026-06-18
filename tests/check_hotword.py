"""验证 FunASR(paraformer-zh=SeACo) 接受 hotword 参数并仍输出分轨。"""

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
HOTWORD = "协和医院 杜斌 瑞芯微 答辩"


def main() -> int:
    clip = config.DATA_DIR / "hotword_clip_60.wav"
    subprocess.run(
        [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(REAL),
         "-t", "60", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)],
        check=True,
    )
    dur = audio.wav_duration_sec(clip)
    print(f"clip={dur:.0f}s, 热词='{HOTWORD}'", flush=True)
    asr = FunASRLocal()
    t = time.time()
    segs = asr.transcribe(clip, dur, hotword=HOTWORD)
    print(f"OK ✅ hotword 参数被接受, {len(segs)}句, 耗时{time.time()-t:.0f}s, "
          f"说话人={sorted({s.speaker for s in segs})}", flush=True)
    for s in segs[:8]:
        print(f"[{s.start}] {s.speaker}: {s.text}", flush=True)
    return 0 if segs else 2


if __name__ == "__main__":
    sys.exit(main())
