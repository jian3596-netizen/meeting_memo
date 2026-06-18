"""全局配置：路径、密钥、服务商选择、模型与音频参数。

所有运行期配置都从项目根目录的 .env 读取（见 .env.example）。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 防止 Windows 上 torch/funasr 触发 libiomp5md.dll 重复初始化导致的崩溃/卡死
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- 目录 ----
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"          # 用户上传的原始文件
PROCESSED_DIR = DATA_DIR / "processed"     # ffmpeg 转码后的 16k 单声道 wav
RESULT_DIR = DATA_DIR / "asr_results"      # ASR 原始返回 JSON（留档）
WEB_DIR = BASE_DIR / "web"
DB_PATH = DATA_DIR / "meeting_memo.db"

for _d in (DATA_DIR, UPLOAD_DIR, PROCESSED_DIR, RESULT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- 密钥与服务商 ----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
# dashscope | fake  （fake 用于无 key / 无网络时跑通整条链路）
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "dashscope").strip().lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()

# ---- 模型 ----
# 云端 ASR（DashScope）模型
ASR_MODEL = os.getenv("ASR_MODEL", "paraformer-v2").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus").strip()
# 本地 ASR（FunASR）模型（首次运行自动从 ModelScope 下载）
FUNASR_ASR_MODEL = os.getenv("FUNASR_ASR_MODEL", "paraformer-zh").strip()
FUNASR_VAD_MODEL = os.getenv("FUNASR_VAD_MODEL", "fsmn-vad").strip()
FUNASR_PUNC_MODEL = os.getenv("FUNASR_PUNC_MODEL", "ct-punc").strip()
FUNASR_SPK_MODEL = os.getenv("FUNASR_SPK_MODEL", "cam++").strip()  # 说话人分轨
# 预设说话人数：留空=自动估计（长音频可能塌缩成 1 人）；填正整数=强制聚类成该人数，更稳
FUNASR_SPK_NUM = os.getenv("FUNASR_SPK_NUM", "").strip()


def funasr_spk_num() -> "int | None":
    return int(FUNASR_SPK_NUM) if FUNASR_SPK_NUM.isdigit() else None
# 通义千问 OpenAI 兼容端点
DASHSCOPE_LLM_BASE_URL = os.getenv(
    "DASHSCOPE_LLM_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).strip()

# ---- 音频参数 ----
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1  # 单声道（说话人分离要求单声道）
# 说话人分离上限约 2 小时；超过则按此秒数切块分别识别（留 5 分钟余量）
DIARIZATION_MAX_SECONDS = 115 * 60
ALLOWED_UPLOAD_EXTS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma",
    ".mp4", ".mov", ".mkv", ".avi", ".webm",  # 视频也抽音轨
}

# ---- LLM 切块阈值 ----
# 转写文本字符数超过此值时走 map-reduce 分块总结，否则单次结构化抽取
LLM_SINGLE_PASS_MAX_CHARS = 40000
LLM_CHUNK_CHARS = 12000

PROMPT_VERSION = "v1"


def asr_is_fake() -> bool:
    return ASR_PROVIDER == "fake"


def llm_is_fake() -> bool:
    return LLM_PROVIDER == "fake"
