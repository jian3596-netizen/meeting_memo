"""端到端验证生产代码路径：embed_spans(wave切片) + db注册/合并 + cosine匹配。

复用已缓存的 vp_clip_900.wav；对其分轨拿到带标签的 spans，然后：
- spk A 前一半语音 -> embed_spans 注册为声纹 "测试甲"
- spk A 后一半、spk B 全部 -> embed_spans
- 用 asr.cosine 对照：A后半应高度匹配"测试甲"，B 应明显更低
- 再注册一次验证 merge_centroids（加权合并）不报错
- 结束清理测试声纹，不污染数据库
"""

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
os.environ["ASR_PROVIDER"] = "funasr"

from app import asr as asr_mod
from app import config, db
from app.asr import FunASRLocal

CLIP = config.DATA_DIR / "vp_clip_900.wav"
TEST_NAME = "测试甲_e2e"


def main() -> int:
    if not CLIP.exists():
        print(f"缺少缓存切片 {CLIP}，请先跑 check_voiceprint.py", flush=True)
        return 1
    db.init_db()
    asr = FunASRLocal()
    model = asr.model

    t = time.time()
    res = model.generate(input=str(CLIP), batch_size_s=300, preset_spk_num=config.funasr_spk_num())
    info = res[0].get("sentence_info") or []
    print(f"分轨完成 ({time.time()-t:.0f}s)，{len(info)} 句", flush=True)

    by_spk = defaultdict(list)
    for s in info:
        st, en = s.get("start", 0) / 1000.0, s.get("end", 0) / 1000.0
        if en - st >= 1.2:
            by_spk[int(s.get("spk", 0))].append((st, en))
    ranked = sorted(by_spk.items(), key=lambda kv: -len(kv[1]))
    if len(ranked) < 2:
        print("有效说话人不足 2 个", flush=True)
        return 1
    (spkA, spansA), (spkB, spansB) = ranked[0], ranked[1]
    spansA.sort(key=lambda se: se[1] - se[0], reverse=True)
    half = len(spansA) // 2
    A1, A2 = spansA[:half], spansA[half:]
    print(f"spkA={spkA}({len(spansA)}段)  spkB={spkB}({len(spansB)}段)", flush=True)

    # 1) embed_spans（生产路径：wave 切片 + spk_model.inference + 聚合归一化）
    embA1 = asr.embed_spans(CLIP, A1)
    embA2 = asr.embed_spans(CLIP, A2)
    embB = asr.embed_spans(CLIP, spansB)
    assert embA1 and len(embA1) == 192, "embA1 维度异常"
    assert embA2 and embB, "embed_spans 返回空"
    print("embed_spans OK：均为 192 维归一化向量", flush=True)

    # 2) 注册到真实 db（注意结束会清理）
    db.upsert_voiceprint(TEST_NAME, embA1, len(A1))
    enrolled = {v["name"]: v["emb"] for v in db.get_voiceprints()}
    assert TEST_NAME in enrolled, "注册后查不到"

    # 3) 匹配：A 后半 vs B，对照注册的“测试甲”
    sA = asr_mod.cosine(embA2, enrolled[TEST_NAME])
    sB = asr_mod.cosine(embB, enrolled[TEST_NAME])
    print(f"\n余弦相似度对照（阈值 {config.VOICEPRINT_THRESHOLD}）:", flush=True)
    print(f"  A后半 vs 测试甲 = {sA:.3f}   （应高，>= 阈值）", flush=True)
    print(f"  B    vs 测试甲 = {sB:.3f}   （应低）", flush=True)
    print(f"  区分间距 = {sA - sB:.3f}", flush=True)

    # 4) merge_centroids（重复注册增强）
    merged = asr_mod.merge_centroids(embA1, len(A1), embA2, len(A2))
    db.upsert_voiceprint(TEST_NAME, merged, len(A1) + len(A2))
    row = db.get_voiceprint_by_name(TEST_NAME)
    print(f"\nmerge 后 sample_count = {row['sample_count']}（应={len(A1)+len(A2)}）", flush=True)

    ok = sA >= config.VOICEPRINT_THRESHOLD and (sA - sB) >= config.VOICEPRINT_MARGIN
    print(f"\n结论：{'✅ 端到端可行（A认得出、B区分得开）' if ok else '⚠ 该样本区分不足，需调阈值/样本'}", flush=True)

    # 5) 清理
    for v in db.list_voiceprints():
        if v["name"] == TEST_NAME:
            db.delete_voiceprint(v["id"])
    print("已清理测试声纹。", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
