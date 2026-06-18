"""用真实录音的前 N 秒快速验证本地 FunASR（含分轨）。

uv run python tests/check_funasr_real.py        # 默认前 150s
TEST_MAX_SEC=0 ...                               # 0 表示整段
"""

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
from app.textproc import clean_segments

REAL = Path("E:/Git/meeting_memo/temp/20230515 注册问题（欧阳老师）.m4a")
MAX_SEC = int(os.getenv("TEST_MAX_SEC", "150"))


def main() -> int:
    if not REAL.exists():
        print("找不到测试文件:", REAL, flush=True)
        return 1

    clip = config.DATA_DIR / (f"funasr_clip_{MAX_SEC}.wav" if MAX_SEC else "funasr_full.wav")
    print(f"裁剪{'前 ' + str(MAX_SEC) + 's' if MAX_SEC else '整段'} → 16k 单声道 wav …", flush=True)
    t0 = time.time()
    cmd = [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(REAL)]
    if MAX_SEC:
        cmd += ["-t", str(MAX_SEC)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)]
    subprocess.run(cmd, check=True)
    dur = audio.wav_duration_sec(clip)
    print(f"  clip={dur:.1f}s, 转码耗时 {time.time()-t0:.1f}s", flush=True)

    print("加载 FunASR 模型（已缓存）…", flush=True)
    t1 = time.time()
    asr = FunASRLocal()
    print(f"  模型就绪 {time.time()-t1:.1f}s", flush=True)

    print("识别中 …", flush=True)
    t2 = time.time()
    segs = asr.transcribe(clip, dur)
    el = time.time() - t2
    print(f"  完成: {len(segs)} 句, 耗时 {el:.1f}s, RTF={el/dur:.2f}x", flush=True)

    clean_segments(segs)
    print("识别出说话人:", sorted({s.speaker for s in segs}), flush=True)
    print("\n--- 前 20 句 ---", flush=True)
    for s in segs[:20]:
        print(f"[{s.start}] {s.speaker}: {s.text}", flush=True)
    return 0 if segs else 2


if __name__ == "__main__":
    sys.exit(main())
