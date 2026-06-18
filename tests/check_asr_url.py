"""验证 ASR 提交/解析代码本身是否正确：用官方公网示例音频。"""

import sys
from http import HTTPStatus
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import config
from app.asr import _get, _parse_paraformer_json

SAMPLE = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"


def main() -> int:
    import dashscope
    import requests
    dashscope.api_key = config.DASHSCOPE_API_KEY
    from dashscope.audio.asr import Transcription

    task = Transcription.async_call(
        model=config.ASR_MODEL,
        file_urls=[SAMPLE],
        language_hints=["zh", "en"],
        diarization_enabled=True,
    )
    task_id = _get(_get(task, "output"), "task_id")
    print("task_id =", task_id)
    result = Transcription.wait(task=task_id)
    output = _get(result, "output")
    print("task_status =", _get(output, "task_status"))
    for item in _get(output, "results") or []:
        print("subtask =", _get(item, "subtask_status"))
        url = _get(item, "transcription_url")
        if not url:
            continue
        data = requests.get(url, timeout=60).json()
        segs = _parse_paraformer_json(data, 0.0, 0)
        print(f"parsed segments = {len(segs)}")
        for s in segs:
            print(f"  {s.speaker} [{s.start}-{s.end}] {s.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
