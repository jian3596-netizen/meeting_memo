"""真实 DashScope 联调检查：通义千问 LLM + Paraformer 录音文件识别（file:// 本地直传）。

运行：uv run python tests/check_dashscope.py
会消耗少量额度。ASR 用纯音调测试，可能返回 0 句（无人声），只要调用链路通过即算 OK。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import asr, config, textproc
from app.asr import get_asr
from app.llm import get_llm


def main() -> int:
    print(f"ASR_PROVIDER={config.ASR_PROVIDER} LLM_PROVIDER={config.LLM_PROVIDER} "
          f"key={'set' if config.DASHSCOPE_API_KEY else 'MISSING'}")
    if not config.DASHSCOPE_API_KEY:
        print("没有 key，跳过真实联调")
        return 1

    # ---- 1) 通义千问 LLM ----
    print("\n[1] 通义千问结构化纪要 …")
    segs = asr.FakeASR().transcribe(Path("x"), 60)
    textproc.clean_segments(segs)
    try:
        summary = get_llm().summarize(segs, "technical")
        print(f"  LLM OK ✅  title={summary.title!r}")
        print(f"  decisions={len(summary.decisions)} todos={len(summary.todos)} "
              f"risks={len(summary.risks)} open_q={len(summary.open_questions)}")
        for t in summary.todos:
            print(f"    todo: owner={t.owner!r} task={t.task!r} ddl={t.deadline!r} src={t.source_time!r}")
    except Exception as e:
        print(f"  LLM FAILED ❌ {type(e).__name__}: {e}")
        return 2

    # ---- 2) Paraformer ASR（file:// 本地） ----
    print("\n[2] Paraformer 录音文件识别（本地 file://）…")
    sample = config.DATA_DIR / "sample_tone.wav"
    if not sample.exists():
        from app import audio
        subprocess.run(
            [audio_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-ac", "1", "-ar", "16000", str(sample)],
            check=True,
        )
    try:
        res = get_asr().transcribe(sample, 5)
        print(f"  ASR 调用链路 OK ✅  返回句子数={len(res)}（纯音调无人声，0 句属正常）")
        for s in res[:3]:
            print(f"    {s.speaker} [{s.start}] {s.text[:30]}")
    except Exception as e:
        print(f"  ASR FAILED ❌ {type(e).__name__}: {e}")
        return 3

    print("\n真实联调通过 ✅")
    return 0


def audio_ffmpeg() -> str:
    from app import audio
    return audio.ffmpeg_exe()


if __name__ == "__main__":
    sys.exit(main())
