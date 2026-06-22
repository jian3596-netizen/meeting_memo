# AI 会议纪要系统（上传音频文件版 · MVP）

上传会议音频 → **本地**转写 + 说话人分轨 → AI 结构化纪要 + 待办提取 → 在线查看/改名/导出，
每条结论可点击时间戳回听原音频。单机运行，音频不出本机。

## 技术栈

- **后端**：FastAPI + SQLite（stdlib sqlite3）+ 后台线程异步处理
- **音频**：imageio-ffmpeg 自带 ffmpeg（无需系统安装），统一转 16kHz 单声道 wav（视频自动抽音轨）
- **ASR + 说话人分轨**：**本地 FunASR**（Paraformer + fsmn-vad + ct-punc + cam++），离线、音频不出本机；**GPU 自动启用，无 GPU 回落 CPU**
- **LLM 纪要**：**OpenAI 兼容端点**（URL + Key + 模型名由 .env 配置，默认 DeepSeek），JSON 强约束 + source_time 防幻觉
- **前端**：单页原生 HTML/JS（无构建步骤）
- **导出**：Markdown / Word（docx）

> 说明：音频完全在本机转写；但**转写后的文本**会发往你配置的总结 LLM（默认 DeepSeek）。要全本地可把 `LLM_BASE_URL` 指向本地 LLM（Ollama 等 OpenAI 兼容服务）。

## 快速开始

1. 根目录 `.env`（从 `.env.example` 复制；总结 LLM 的 URL/Key 必填）：
   ```env
   LLM_BASE_URL=https://api.deepseek.com/v1   # OpenAI 兼容端点，可换任意兼容服务
   LLM_API_KEY=sk-xxxx                        # 对应服务的 API Key
   LLM_MODEL=deepseek-v4-pro                  # 模型名（按服务实际可用模型填）
   FUNASR_DEVICE=auto                         # auto=有 GPU 自动用 GPU，否则 CPU；也可写死 cpu/cuda
   FUNASR_SPK_NUM=3                           # 预设说话人数：留空=自动估计，填数字更稳
   ```
2. 安装依赖并启动（torch / torchaudio 已在依赖里，`uv sync` 一并装好）：
   ```bash
   uv sync
   uv run python main.py
   ```
3. 浏览器打开 http://127.0.0.1:8000 ，上传音频。

> 首次转写会从 ModelScope 自动下载模型（~1.3GB），之后缓存复用、可离线转写。

## Docker 部署

镜像内置 **CPU 版 PyTorch**（不含 CUDA），ffmpeg 用 imageio 自带二进制，宿主机无需装任何东西。
模型（~2.4GB）与会议数据通过**挂载卷**持久化，不打进镜像。

> 仅在 x86_64 Linux 验证；Apple Silicon / ARM 需自行确认 wheel 可用。

### 1. 准备 .env

```bash
cp .env.example .env        # 然后填入 LLM_BASE_URL / LLM_API_KEY（总结用）
```

### 2. docker compose（推荐）

```bash
docker compose up -d --build      # 构建并后台启动
docker compose logs -f            # 看日志（首次会下载模型 ~2.4GB）
```
打开 http://localhost:8000 。改完代码重建：`docker compose up -d --build`；停止：`docker compose down`。

### 3. 或手动 build / run

```bash
docker build -t meeting-memo:latest .          # 打包

docker run -d --name meeting-memo \            # 运行
  -p 8000:8000 \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/models:/app/models" \
  --restart unless-stopped \
  meeting-memo:latest
```

### 挂载卷

| 宿主机 | 容器内 | 内容 |
| --- | --- | --- |
| `./data` | `/app/data` | SQLite、上传原文件、转码 wav、ASR 留档 |
| `./models` | `/app/models` | FunASR/ModelScope 模型缓存（`MODELSCOPE_CACHE`），重建容器不丢、不重下 |

### 预热模型（可选）

首次转写会联网下载 ~2.4GB 模型到 `./models`，第一场会议会比较慢；之后复用缓存、可离线转写。
想提前下好：

```bash
docker compose run --rm meeting-memo \
  python -c "from app.asr import FunASRLocal; FunASRLocal(); print('模型就绪')"
```

### 迁移到内网 / 离线机

在有网机器导出镜像，连同预热好的 `./models` 一起拷到目标机：

```bash
docker save meeting-memo:latest | gzip > meeting-memo.tar.gz   # 有网机：导出
docker load < meeting-memo.tar.gz                              # 目标机：导入
# 再把 ./models 目录拷过去挂载即用（总结仍需能访问你配置的 LLM 端点，否则改本地 LLM）
```

### 常见问题

- **首场会议一直“转写中”**：多半在下模型，`docker compose logs -f` 看进度，下完即恢复。
- **总结失败**：检查 `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（总结走你配置的 OpenAI 兼容端点，需联网）。要全离线可把 `LLM_BASE_URL` 指向本地 LLM（Ollama 等）。
- **构建报 torch 版本找不到**：把 Dockerfile 里 `torch==2.12.0 torchaudio==2.11.0` 的精确版本去掉，让 CPU 源自动选。
- **想用 GPU**：当前是 CPU 镜像；需换 CUDA 基础镜像 + GPU 版 torch，并以 `--gpus all` 运行。

## 性能基线（参考：i5-1335U，无 GPU）

- FunASR RTF ≈ 0.33x（40 分钟音频 ≈ 13 分钟 CPU 推理）
- 模型每进程首次加载 ≈ 140s，之后进程内复用

## API（PRD 第 7 节）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/meetings` | 上传音频（multipart：file + template_type） |
| GET | `/api/meetings` | 会议列表 |
| GET | `/api/meetings/{id}/status` | 处理状态 + 进度 |
| GET | `/api/meetings/{id}/transcript` | 转写全文（带说话人/时间戳） |
| GET | `/api/meetings/{id}/summary` | 结构化纪要 JSON |
| PUT | `/api/meetings/{id}/summary` | 保存编辑后的纪要 |
| POST | `/api/meetings/{id}/regenerate` | 换模板/加指令重新生成（仅重跑总结） |
| POST | `/api/meetings/{id}/speakers` | 说话人改名 |
| GET | `/api/meetings/{id}/audio` | 音频流（时间戳回听） |
| GET | `/api/meetings/{id}/export?format=md\|docx` | 导出 |
| GET | `/api/hotwords` ｜ PUT | 热词词库 读取 / 保存 |
| GET | `/api/voiceprints` | 声纹库列表 |
| DELETE | `/api/voiceprints/{vid}` | 删除某声纹 |
| POST | `/api/meetings/{id}/voiceprints` | 从该会议某说话人注册声纹（body：speaker + name） |

## 声纹 · 说话人自动识别

给每个人存一份声纹（cam++ 192 维 embedding），以后新会议分轨后**自动匹配命名**，免去每次手动改名。

- **注册**：在某会议详情里给说话人填好真实姓名 → 点该行「存声纹」。系统聚合该说话人在本场的多段语音成一个声纹中心存库；同名再注册会按样本数加权增强。
- **自动识别**：新会议转写后，每个 `SPEAKER_xx` 算声纹中心与声纹库逐一比对，余弦相似度超阈值且 Top1 明显高于 Top2 才认；**认不出就保留 `SPEAKER_xx` 等你手动命名**，且**不会覆盖你已手动改的名字**。
- **实测**（真实 3 人录音）：同人余弦均值 ≈ 0.52、异人 ≈ 0.30，注册→识别准确率 ≈ 95%。单句会有重叠，所以采用「多段聚合 + 保守阈值 + 不确定不认」策略。
- 复用本地 cam++ 模型抽声纹，不额外加载模型。
- 可调参数（`.env`）：`VOICEPRINT_THRESHOLD`（默认 0.50）、`VOICEPRINT_MARGIN`（默认 0.06）、`VOICEPRINT_MAX_SEG`（默认 20）、`VOICEPRINT_ENABLED`（默认开）。

## 已知限制

- 说话人分轨：短音频自动估计良好；**长音频自动估计可能塌缩成 1 人**，建议用 `FUNASR_SPK_NUM` 指定人数。
- 无 GPU 时长音频较慢（见性能基线）；分轨依赖单声道。
- pdf 导出暂未实现（先 md/docx）。
- Windows 上已通过 `KMP_DUPLICATE_LIB_OK=TRUE`（在 `app/config.py` 自动设置）规避 OpenMP 冲突。
- 会议模板：通用 / 项目 / 客户拜访 / 技术评审（PRD 第 5 节）。

## 自测脚本

- `tests/check_funasr_real.py`：真实录音前 N 秒本地 FunASR 验证（`TEST_MAX_SEC` 控制秒数）
- `tests/check_diar.py`：对比自动估计 vs 预设说话人数
- `tests/check_voiceprint.py`：声纹可行性实验（同人/异人相似度分布 + 识别准确率，`VP_CLIP_SEC` 控制时长）
- `tests/check_voiceprint_e2e.py`：声纹端到端（embed_spans + db 注册/合并 + 匹配）
- `tests/show_result.py <meeting_id>`：打印某会议已生成的纪要
