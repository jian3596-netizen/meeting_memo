"""ASR 子进程 worker：在独立进程里加载 FunASR 跑转写 / 抽声纹，跑完即退出，
内存（torch+funasr 库 ~1.5G + 模型权重 ~2G）随进程结束全部还给 OS。

用法： python -m app.asr_worker <req.json> <out.json>

req（转写）: {"op":"transcribe","wav":"...","duration":0.0,"hotword":"","spk_num":null,"want_embeddings":false}
req（声纹）: {"op":"embed","wav":"...","spans":[[start_s,end_s],...]}

out（转写）: {"segments":[{...Segment...}], "embeddings":{"SPEAKER_00":[...192维...], ...}}
out（声纹）: {"embedding":[...192维...] 或 null}

结果只写到 out.json；FunASR/modelscope 往 stdout/stderr 打的日志不影响结果解析。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def _run(req: dict) -> dict:
    from .asr import FunASRLocal  # 仅在子进程里 import，主进程不会加载这些重库

    asr = FunASRLocal()
    op = req.get("op")

    if op == "transcribe":
        segs = asr.transcribe(
            Path(req["wav"]), float(req.get("duration") or 0.0),
            hotword=req.get("hotword", ""), spk_num=req.get("spk_num"),
        )
        out = {"segments": [s.model_dump() for s in segs]}
        if req.get("want_embeddings"):
            spans = defaultdict(list)
            for s in segs:
                spans[s.speaker].append((s.start_seconds, s.end_seconds))
            emb = {}
            for spk, sp in spans.items():
                v = asr.embed_spans(Path(req["wav"]), sp)
                if v is not None:
                    emb[spk] = v
            out["embeddings"] = emb
        return out

    if op == "embed":
        spans = [tuple(x) for x in req.get("spans", [])]
        return {"embedding": asr.embed_spans(Path(req["wav"]), spans)}

    return {"error": f"unknown op: {op}"}


def main() -> None:
    req_path, out_path = sys.argv[1], sys.argv[2]
    with open(req_path, encoding="utf-8") as f:
        req = json.load(f)
    out = _run(req)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
