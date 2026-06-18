"""信道/设备失配实验：同一段语音模拟成不同设备来源，看声纹相似度掉多少。

做法（受控变量）：对原始录音取若干固定时间窗，把整段经 ffmpeg 变换成"不同设备"版本
（电话窄带 / mp3低码率 / aac低码率 / 加噪 / 远讲混响），再对同一时间窗抽声纹，
与原始版本算余弦。因为说话内容完全相同，相似度下降 = 纯信道影响。

注意：真实"同一人不同设备"还叠加了"说话内容不同"，所以实际会比这里更低——
本实验给的是信道影响的【上界(最乐观)】。结合之前"同设备不同内容≈0.93"一起判断。
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
os.environ["ASR_PROVIDER"] = "funasr"

from app import audio, config
from app.asr import FunASRLocal, cosine

REAL = Path("E:/Git/meeting_memo/temp/20230515 注册问题（欧阳老师）.m4a")
CLIP = 360
WINDOWS = [(40, 80), (110, 150), (180, 220), (250, 290), (320, 355)]
FF = audio.ffmpeg_exe()
D = config.DATA_DIR


def _ff(args, out):
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-t", str(CLIP),
                    "-i", str(REAL), *args, "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out)], check=True)


def _ff_codec(codec_args, ext, out):
    mid = D / f"ch_mid.{ext}"
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-t", str(CLIP),
                    "-i", str(REAL), *codec_args, str(mid)], check=True)
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(mid),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)], check=True)


def build():
    variants = {}
    out = D / "ch_orig.wav"; _ff([], out); variants["原始(基准)"] = out
    try:
        out = D / "ch_phone.wav"
        _ff(["-af", "highpass=f=300,lowpass=f=3400,aresample=8000,aresample=16000"], out)
        variants["电话窄带8k"] = out
    except Exception as e: print("phone 失败", e, flush=True)
    try:
        out = D / "ch_mp3.wav"; _ff_codec(["-c:a", "libmp3lame", "-b:a", "32k"], "mp3", out)
        variants["mp3@32k"] = out
    except Exception as e: print("mp3 失败", e, flush=True)
    try:
        out = D / "ch_aac.wav"; _ff_codec(["-c:a", "aac", "-b:a", "32k"], "m4a", out)
        variants["aac@32k"] = out
    except Exception as e: print("aac 失败", e, flush=True)
    try:
        out = D / "ch_noise.wav"
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-t", str(CLIP),
                        "-i", str(REAL), "-filter_complex",
                        "anoisesrc=color=pink:amplitude=0.02:d=360[n];[0:a][n]amix=inputs=2:duration=shortest",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)], check=True)
        variants["加底噪"] = out
    except Exception as e: print("noise 失败", e, flush=True)
    try:
        out = D / "ch_far.wav"; _ff(["-af", "volume=0.35,aecho=0.8:0.6:35:0.25"], out)
        variants["远讲+混响"] = out
    except Exception as e: print("far 失败", e, flush=True)
    return variants


def main() -> int:
    if not REAL.exists():
        print("缺少原始录音", flush=True)
        return 1
    print("生成各设备版本 …", flush=True)
    variants = build()
    asr = FunASRLocal()

    # 原始各窗的声纹
    ref = {}
    for w in WINDOWS:
        ref[w] = asr.embed_spans(variants["原始(基准)"], [w], max_segments=1, min_dur=2.0)

    print("\n=== 同一段话，不同设备来源 vs 原始 的声纹余弦 ===", flush=True)
    print(f"（阈值 {config.VOICEPRINT_THRESHOLD}；越接近1越稳，越低越说明设备影响大）\n", flush=True)
    for name, wav in variants.items():
        if name == "原始(基准)":
            continue
        sims = []
        for w in WINDOWS:
            a = ref[w]
            b = asr.embed_spans(wav, [w], max_segments=1, min_dur=2.0)
            if a and b:
                sims.append(cosine(a, b))
        if sims:
            import numpy as np
            arr = np.array(sims)
            flag = "✅同人" if arr.mean() >= config.VOICEPRINT_THRESHOLD else "⚠可能认不出"
            print(f"{name:12s} mean={arr.mean():.3f}  min={arr.min():.3f}  {flag}", flush=True)
    print("\n参考：同设备不同内容(同一人) ≈ 0.93；异人 ≈ 0.47。", flush=True)
    print("解读：以上是'同内容'最乐观值；真实跨设备还要再叠加内容差异，会更低。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
