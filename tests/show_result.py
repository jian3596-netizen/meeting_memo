"""打印某个会议已生成的纪要（从 SQLite 读，不重跑）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import db, export, pipeline

MID = sys.argv[1] if len(sys.argv) > 1 else "68b993c480714c58a392cd994af80596"


def main() -> int:
    m = db.get_meeting(MID)
    if not m:
        print("会议不存在:", MID)
        return 1
    s = pipeline.load_summary(MID)
    segs = pipeline.load_segments(MID)
    speakers = sorted({x.speaker for x in segs})
    print(f"标题: {m['title']}")
    print(f"时长: {m['duration_sec']:.0f}s  片段: {len(segs)}  说话人: {speakers}")
    print(f"状态: {m['status']}")
    if not s:
        print("（纪要未生成）")
        return 2
    md = export.to_markdown(m, s, segs)
    Path("data/result.md").write_text(md, encoding="utf-8")
    print(f"markdown 已存 data/result.md ({len(md.encode('utf-8'))} bytes)")
    print("\n========== 结构化纪要 ==========")
    print("【标题】", s.title)
    print("【摘要】", s.summary)
    print(f"\n讨论点 {len(s.topics)} / 决策 {len(s.decisions)} / 待办 {len(s.todos)} / "
          f"风险 {len(s.risks)} / 未决 {len(s.open_questions)}")
    for d in s.decisions:
        print(f"  [决策] {d.content}  ({d.source_time})")
    for t in s.todos:
        print(f"  [待办] 负责人={t.owner} | {t.task} | 截止={t.deadline} | {t.source_time}")
    for r in s.risks:
        print(f"  [风险] {r.content}  ({r.source_time})")
    for q in s.open_questions:
        print(f"  [未决] {q.content}  ({q.source_time})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
