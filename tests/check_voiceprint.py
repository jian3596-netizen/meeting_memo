"""声纹可行性实验：用真实录音验证 cam++ embedding 能否区分说话人。

做三件事：
1) 对录音前 VP_CLIP_SEC 秒分轨，得到带 spk 标签的句子。
2) 复用 model.spk_model（CAMPPlus）对每句切片抽 192 维声纹。
3) 统计 同人 vs 不同人 的余弦相似度分布；再模拟"注册(半数)→识别(另一半)"的准确率。

结论看：同人均值是否明显高于不同人均值、识别准确率、以及推荐阈值。
"""

import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
os.environ["ASR_PROVIDER"] = "funasr"

from app import audio, config
from app.asr import FunASRLocal

REAL = Path("E:/Git/meeting_memo/temp/20230515 注册问题（欧阳老师）.m4a")
CLIP_SEC = int(os.getenv("VP_CLIP_SEC", "900"))      # 取前多少秒做实验
MAX_SEG_PER_SPK = int(os.getenv("VP_MAX_SEG", "16")) # 每个说话人最多取多少条（取最长的）
MIN_SEG_DUR = 1.2                                    # 太短的句子不可靠，丢弃


def read_wav_f32(path: Path):
    import wave
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def cos(a, b):
    a = a / (np.linalg.norm(a) + 1e-8)
    b = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a, b))


def main() -> int:
    clip = config.DATA_DIR / f"vp_clip_{CLIP_SEC}.wav"
    if not clip.exists():
        print(f"切片前 {CLIP_SEC}s …", flush=True)
        subprocess.run(
            [audio.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(REAL),
             "-t", str(CLIP_SEC), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)],
            check=True,
        )

    t = time.time()
    asr = FunASRLocal()                       # 加载模型（首次约 140s）
    model = asr.model
    print(f"模型就绪 ({time.time()-t:.0f}s)，开始分轨 …", flush=True)

    t = time.time()
    res = model.generate(input=str(clip), batch_size_s=300,
                         preset_spk_num=config.funasr_spk_num())
    info = res[0].get("sentence_info") or []
    print(f"分轨完成 ({time.time()-t:.0f}s)，共 {len(info)} 句", flush=True)

    wavf, sr = read_wav_f32(clip)
    spk_model = model.spk_model
    device = model.kwargs.get("device", "cpu")

    def embed(st, en):
        a, b = int(st * sr), int(en * sr)
        seg = wavf[a:b]
        if len(seg) < int(0.5 * sr):
            return None
        out, _ = spk_model.inference(data_in=[seg], key=["x"], device=device, fs=16000)
        return out[0]["spk_embedding"].detach().cpu().numpy().reshape(-1)

    # 按说话人分组，取最长的若干句
    by_spk = defaultdict(list)
    for s in info:
        st, en = s.get("start", 0) / 1000.0, s.get("end", 0) / 1000.0
        if en - st >= MIN_SEG_DUR:
            by_spk[int(s.get("spk", 0))].append((en - st, st, en))

    embs = {}  # spk -> list[np.ndarray]
    for spk, segs in sorted(by_spk.items()):
        segs.sort(reverse=True)                       # 最长的优先
        vs = []
        for _, st, en in segs[:MAX_SEG_PER_SPK]:
            v = embed(st, en)
            if v is not None:
                vs.append(v)
        if len(vs) >= 4:                              # 至少 4 条才进入统计
            embs[spk] = vs
    print(f"参与统计的说话人: {[ (f'SPK{spk}', len(v)) for spk, v in embs.items() ]}", flush=True)
    if len(embs) < 2:
        print("⚠ 有效说话人不足 2 个，加大 VP_CLIP_SEC 再试。", flush=True)
        return 1

    # 同人 / 不同人 相似度分布
    intra, inter = [], []
    spks = list(embs)
    for spk in spks:
        vs = embs[spk]
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                intra.append(cos(vs[i], vs[j]))
    for ai in range(len(spks)):
        for bi in range(ai + 1, len(spks)):
            for x in embs[spks[ai]]:
                for y in embs[spks[bi]]:
                    inter.append(cos(x, y))
    intra, inter = np.array(intra), np.array(inter)
    print("\n=== 余弦相似度分布 ===", flush=True)
    print(f"同人  intra: mean={intra.mean():.3f}  min={intra.min():.3f}  p10={np.percentile(intra,10):.3f}", flush=True)
    print(f"异人  inter: mean={inter.mean():.3f}  max={inter.max():.3f}  p90={np.percentile(inter,90):.3f}", flush=True)
    gap = intra.mean() - inter.mean()
    print(f"区分度 gap(同-异 均值) = {gap:.3f}", flush=True)

    # 模拟：每人前一半注册（取 centroid），后一半识别，最近 centroid 即判定
    enroll, test = {}, {}
    for spk, vs in embs.items():
        h = max(1, len(vs) // 2)
        c = np.mean(vs[:h], axis=0)
        enroll[spk] = c / (np.linalg.norm(c) + 1e-8)
        test[spk] = vs[h:]
    correct = total = 0
    margins = []
    for true_spk, vs in test.items():
        for v in vs:
            sims = {spk: cos(v, c) for spk, c in enroll.items()}
            pred = max(sims, key=sims.get)
            ordered = sorted(sims.values(), reverse=True)
            margins.append(ordered[0] - (ordered[1] if len(ordered) > 1 else 0))
            correct += int(pred == true_spk)
            total += 1
    print("\n=== 注册→识别 模拟 ===", flush=True)
    print(f"准确率: {correct}/{total} = {correct/total*100:.1f}%", flush=True)
    print(f"Top1-Top2 余弦差(越大越稳): mean={np.mean(margins):.3f}", flush=True)

    # 阈值建议：同人 p10 与 异人 p90 之间取中点
    lo, hi = np.percentile(inter, 90), np.percentile(intra, 10)
    rec = (lo + hi) / 2
    print("\n=== 阈值建议 ===", flush=True)
    if hi > lo:
        print(f"存在清晰分界：异人p90={lo:.3f} < 同人p10={hi:.3f}，推荐阈值 ≈ {rec:.2f}（保守可取 {hi:.2f}）", flush=True)
        print("结论：✅ 声纹可区分，方案可行。", flush=True)
    else:
        print(f"分界有重叠：异人p90={lo:.3f} ≥ 同人p10={hi:.3f}，需更长样本或更保守阈值（建议 ≥0.60 + 拒判区）", flush=True)
        print("结论：⚠ 有重叠，建议保守阈值 + 不确定就不自动归并。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
