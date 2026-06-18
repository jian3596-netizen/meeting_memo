"""诊断说话人分轨在长音频上的塌缩：同一段音频对比 自动估计 vs 预设人数。"""

import os
import subprocess
import sys
import time
from collections import Counter
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
MAX_SEC = int(os.getenv("TEST_MAX_SEC", "600"))


def spk_dist(res):
    info = res[0].get("sentence_info") or []
    c = Counter(s.get("spk", 0) for s in info)
    return len(info), dict(sorted(c.items()))


def main() -> int:
    clip = config.DATA_DIR / f"diar_clip_{MAX_SEC}.wav"
    subprocess.run(
        [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(REAL),
         "-t", str(MAX_SEC), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)],
        check=True,
    )
    dur = audio.wav_duration_sec(clip)
    print(f"clip={dur:.0f}s", flush=True)

    asr = FunASRLocal()
    model = asr.model

    print("auto（自动估计人数）…", flush=True)
    t = time.time()
    n, d = spk_dist(model.generate(input=str(clip), batch_size_s=300))
    print(f"  自动: {n}句, 说话人分布={d}, 耗时{time.time()-t:.0f}s", flush=True)

    for k in (2, 3, 4):
        print(f"preset_spk_num={k} …", flush=True)
        t = time.time()
        n, d = spk_dist(model.generate(input=str(clip), batch_size_s=300, preset_spk_num=k))
        print(f"  preset={k}: {n}句, 说话人分布={d}, 耗时{time.time()-t:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
