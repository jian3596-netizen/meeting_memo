"""端到端冒烟测试（fake provider，无需 key/网络）。

生成一段测试音频 → 跑完整流水线 → 校验转写/纪要/待办/导出。
运行：uv run python tests/smoke_fake.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
except Exception:
    pass
os.environ.setdefault("ASR_PROVIDER", "fake")
os.environ.setdefault("LLM_PROVIDER", "fake")

import subprocess

from app import audio, config, db, export, pipeline


def main() -> int:
    db.init_db()
    sample = config.DATA_DIR / "sample_tone.wav"
    subprocess.run(
        [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-ac", "1", "-ar", "16000", str(sample)],
        check=True,
    )

    mid = db.create_meeting(
        title="smoke", original_filename="sample_tone.wav",
        audio_path=str(sample), template_type="technical",
    )
    pipeline.process_meeting(mid)

    m = db.get_meeting(mid)
    print(f"status={m['status']} progress={m['progress']} duration={m['duration_sec']:.1f}s")
    if m["status"] != "completed":
        print(f"FAILED step={m['failed_step']} err={m['error_message']}")
        return 1

    segs = pipeline.load_segments(mid)
    summ = pipeline.load_summary(mid)
    tasks = db.get_task_rows(mid)
    print(f"segments={len(segs)} title={summ.title!r} todos={len(summ.todos)} task_rows={len(tasks)}")

    # 说话人改名 + 复查
    db.set_speaker_map(mid, {"SPEAKER_00": "赵健"})
    segs2 = pipeline.load_segments(mid)
    named = [s for s in segs2 if s.speaker == "SPEAKER_00"]
    assert named and named[0].speaker_name == "赵健", "说话人改名未生效"
    print(f"speaker rename OK: {named[0].speaker} -> {named[0].speaker_name}")

    md = export.to_markdown(m, summ, segs2)
    docx = export.to_docx(m, summ, segs2)
    print(f"markdown={len(md)} chars, docx={len(docx)} bytes")
    assert len(md) > 100 and len(docx) > 1000

    db.delete_meeting(mid)
    print("ALL OK ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
