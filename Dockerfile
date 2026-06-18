# syntax=docker/dockerfile:1
# AI 会议纪要系统 —— CPU 版镜像（本地 FunASR + 云 qwen 总结）
FROM python:3.12-slim

# 系统依赖：
#   libgomp1    -> torch 的 OpenMP 运行时
#   libsndfile1 -> torchaudio/funasr 读取音频
#   ca-certificates -> HTTPS（下载模型 / 调用通义千问）
# ffmpeg 不用装系统包：项目用 imageio-ffmpeg 自带的静态二进制。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # 模型缓存目录：挂卷持久化，避免每次重下 ~2.4GB
    MODELSCOPE_CACHE=/app/models \
    # 默认走本地 ASR + 云 qwen 总结（DASHSCOPE_API_KEY 通过 .env 注入）
    ASR_PROVIDER=funasr \
    LLM_PROVIDER=dashscope

WORKDIR /app

# 1) 先装 CPU 版 torch / torchaudio。
#    Linux 上 PyPI 默认的 torch 会牵出整套 CUDA 依赖（数 GB），本项目纯 CPU 用不到，
#    所以显式从 PyTorch CPU 源安装，镜像更小、更快。
RUN pip install --no-cache-dir \
        torch==2.12.0 torchaudio==2.11.0 \
        --index-url https://download.pytorch.org/whl/cpu

# 2) 其余依赖从 PyPI 装（torch 已满足，funasr 不会回拉 CUDA 版）。
#    与 pyproject.toml 的 dependencies 保持一致。
RUN pip install --no-cache-dir \
        "dashscope>=1.25.22" \
        "fastapi>=0.137.1" \
        "funasr>=1.3.9" \
        "imageio-ffmpeg>=0.6.0" \
        "openai>=2.42.0" \
        "python-docx>=1.2.0" \
        "python-dotenv>=1.2.2" \
        "python-multipart>=0.0.32" \
        "uvicorn[standard]>=0.49.0"

# 3) 拷贝应用代码（数据/模型/密钥都不进镜像，运行时挂卷或注入）
COPY app/ ./app/
COPY web/ ./web/
COPY main.py ./

# 数据与模型目录（运行时由挂载卷覆盖）
RUN mkdir -p /app/data /app/models

EXPOSE 8000

# 监听 0.0.0.0，绕开 main.py 里写死的 127.0.0.1（容器内必须对外可达）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
