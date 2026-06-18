"""决定性测试：DashScope 录音文件识别能否吃本地文件（任意形式）。

测试三种本地路径写法，看是否有任意一种被 SDK 自动上传并识别成功。
若全部失败 → 必须用公网 URL（OSS / 隧道）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import config
from app.asr import _get

REAL = Path("E:/Git/meeting_memo/temp/20230515 注册问题（欧阳老师）.m4a").resolve()


def try_variant(label: str, url: str) -> None:
    import dashscope
    dashscope.api_key = config.DASHSCOPE_API_KEY
    from dashscope.audio.asr import Transcription
    print(f"\n=== {label} ===\n  url = {url}")
    try:
        task = Transcription.async_call(
            model=config.ASR_MODEL, file_urls=[url],
            language_hints=["zh", "en"], diarization_enabled=True,
        )
        task_id = _get(_get(task, "output"), "task_id")
        if not task_id:
            print(f"  submit failed: {_get(task, 'message')}")
            return
        result = Transcription.wait(task=task_id)
        output = _get(result, "output")
        status = _get(output, "task_status")
        print(f"  task_status = {status}")
        if status == "SUCCEEDED":
            print("  >>> 成功！本地直传可用 ✅")
        else:
            results = _get(output, "results") or []
            echoed = _get(results[0], "file_url") if results else "?"
            print(f"  code={_get(output, 'code')} echoed_url={echoed}")
    except Exception as e:
        print(f"  EXCEPTION {type(e).__name__}: {e}")


def main() -> int:
    if not REAL.exists():
        print("找不到测试文件:", REAL)
        return 1
    print("file size MB:", round(REAL.stat().st_size / 1e6, 1))
    try_variant("v1 plain windows path", str(REAL))
    try_variant("v2 file:// uri", REAL.as_uri())
    try_variant("v3 posix path", REAL.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
