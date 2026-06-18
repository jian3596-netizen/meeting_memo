# AI 会议纪要系统技术路线：上传音频文件版

## 1. 产品目标

用户上传会议音频文件，系统自动完成：

- 音频解析
- 语音转文字
- 说话人区分
- 会议内容分段
- AI 纪要生成
- 待办事项提取
- 原文时间戳追溯
- Markdown / Word 导出

第一版不做实时录音、不做实时转写，只做上传文件后的异步处理。

---

## 2. 核心流程

用户上传音频
  ↓
音频文件校验
  ↓
音频格式转换
  ↓
音频预处理
  ↓
语音转文字 ASR
  ↓
说话人区分 Diarization
  ↓
转写文本清洗
  ↓
按时间 / 主题切块
  ↓
大模型生成结构化纪要
  ↓
提取决策、待办、风险、问题
  ↓
前端展示 / 编辑 / 导出

---

## 3. 系统模块划分

### 3.1 前端模块

负责用户操作和结果展示。

功能：

- 上传音频文件
- 查看处理状态
- 查看转写全文
- 查看 AI 纪要
- 编辑纪要内容
- 查看待办事项
- 点击时间戳回听原音频
- 导出 Markdown / Word / PDF

页面建议：

- 会议列表页
- 音频上传页
- 会议详情页
  - 左侧：转写全文
  - 右侧：AI 纪要
  - 下方：待办事项
  - 顶部：重新生成 / 选择模板 / 导出

---

### 3.2 后端 Gateway 模块

负责统一接口、任务调度和数据管理。

核心职责：

- 接收音频上传
- 创建会议任务
- 管理任务状态
- 调用音频处理服务
- 调用 ASR 服务
- 调用 LLM 服务
- 保存转写和纪要结果
- 提供前端查询接口

建议技术栈：

- FastAPI
- SQLite / PostgreSQL
- Redis，可选
- Celery / RQ / Dramatiq，可选
- 本地文件系统 / MinIO，用于保存音频文件

---

### 3.3 音频处理模块

负责把用户上传的各种音频文件统一成标准格式。

输入格式：

- mp3
- wav
- m4a
- aac
- flac
- mp4 视频文件，可选

处理步骤：

- 文件格式识别
- 音频抽取
- 转成 wav
- 统一采样率，例如 16kHz
- 单声道处理
- 音量归一化
- 简单降噪，可选
- VAD 静音切分，可选

推荐工具：

- ffmpeg
- pydub
- librosa，可选

标准输出：

- cleaned_audio.wav
- audio_duration
- audio_segments

---

### 3.4 ASR 语音转文字模块

负责把音频转换成文字。

核心要求：

- 支持中文
- 支持中英文混说
- 支持时间戳
- 支持标点
- 支持热词
- 支持长音频分段转写

输出格式建议：

    [
      {
        "start": "00:00:03",
        "end": "00:00:08",
        "speaker": "SPEAKER_00",
        "text": "我们今天主要讨论一下前后端接口的问题。"
      },
      {
        "start": "00:00:09",
        "end": "00:00:15",
        "speaker": "SPEAKER_01",
        "text": "我建议先把通讯格式固定下来。"
      }
    ]

ASR 方案：

#### 方案 A：云 ASR

优点：

- 效果好
- 开发快
- 支持热词
- 长音频能力成熟

缺点：

- 有费用
- 音频需要上传云端
- 私有化能力弱

适合 MVP。

#### 方案 B：本地 ASR

优点：

- 数据安全
- 可离线部署
- 成本可控

缺点：

- 部署复杂
- 对硬件性能有要求
- 热词和标点效果可能需要额外优化

适合后续私有化版本。

---

### 3.5 说话人区分模块

负责区分不同发言人。

第一版可以做到：

- SPEAKER_00
- SPEAKER_01
- SPEAKER_02

不强求自动识别真实姓名。

后续可以增加：

- 手动绑定姓名
- 记住历史绑定关系
- 声纹识别
- 组织通讯录匹配

输出示例：

    SPEAKER_00 = 赵健
    SPEAKER_01 = 软件工程师
    SPEAKER_02 = 硬件工程师

第一版建议：

- 先做 speaker diarization
- 再提供人工修改姓名功能
- 不要一开始追求自动识别人名

---

### 3.6 文本清洗模块

ASR 输出不能直接给大模型，需要先清洗。

处理内容：

- 去除重复语气词
- 修正明显断句
- 合并过短句子
- 保留时间戳
- 保留说话人
- 保留原始文本，不覆盖
- 生成清洗版文本

注意：

原始转写必须保留，因为它是 AI 纪要的证据来源。

建议保存两份：

- raw_transcript：原始转写
- clean_transcript：清洗后转写

---

### 3.7 长文本切块模块

会议转写通常很长，不能直接一次性丢给大模型。

建议切块方式：

- 按时间切块，例如每 5–10 分钟
- 按 token 数切块，例如每 3000–5000 tokens
- 按主题切块，可作为后续增强

切块数据结构：

    [
      {
        "chunk_id": 1,
        "start": "00:00:00",
        "end": "00:10:00",
        "text": "..."
      },
      {
        "chunk_id": 2,
        "start": "00:10:00",
        "end": "00:20:00",
        "text": "..."
      }
    ]

处理策略：

- 每个 chunk 先生成小摘要
- 所有小摘要再合并成总纪要
- 待办、决策、风险单独提取
- 最终结果必须带时间戳证据

---

## 4. AI 纪要生成模块

### 4.1 纪要生成目标

不要只生成普通摘要，而是生成结构化内容。

建议输出：

- 会议标题
- 会议摘要
- 关键讨论点
- 已确认决策
- 待办事项
- 风险问题
- 未决问题
- 重要原文引用
- 时间戳来源

---

### 4.2 标准 JSON 输出格式

    {
      "title": "RK3399 与 RK3588 前后端架构讨论",
      "summary": "本次会议主要讨论了前端页面固定、后端 gateway 服务、多 Agent 实现方式以及接口标准化设计。",
      "topics": [
        {
          "title": "前后端接口设计",
          "summary": "团队倾向于先固定前后端通信格式，再测试不同后端实现。",
          "source_time": "00:12:30"
        }
      ],
      "decisions": [
        {
          "content": "先定义统一 API，再分别实现本地模型、云模型和多 Agent 后端。",
          "source_time": "00:18:45"
        }
      ],
      "todos": [
        {
          "owner": "赵健",
          "task": "整理前后端接口 v0.1 草案",
          "deadline": "本周五",
          "source_time": "00:22:10"
        }
      ],
      "risks": [
        {
          "content": "如果接口设计过早固定，后续 Agent 能力扩展可能受限。",
          "source_time": "00:31:20"
        }
      ],
      "open_questions": [
        {
          "content": "本地 ASR 是否部署在 RK3588 上，还是先调用云端服务？",
          "source_time": "00:36:05"
        }
      ]
    }

---

## 5. 会议模板设计

不同会议类型使用不同模板。

### 5.1 通用会议模板

包含：

- 会议摘要
- 关键讨论
- 结论
- 待办
- 风险
- 未决问题

### 5.2 项目会议模板

包含：

- 项目进展
- 当前阻塞
- 关键决策
- 负责人
- 截止时间
- 下次会议前动作

### 5.3 客户拜访模板

包含：

- 客户背景
- 客户需求
- 客户异议
- 预算/时间线
- 竞品信息
- 下一步跟进

### 5.4 技术评审模板

包含：

- 方案背景
- 技术路线
- 争议点
- 决策结果
- 风险
- 后续验证项

---

## 6. 数据库设计建议

### 6.1 meetings 表

字段：

- id
- title
- audio_file_path
- duration
- status
- template_type
- created_at
- updated_at

status 可选值：

- uploaded
- processing_audio
- transcribing
- summarizing
- completed
- failed

---

### 6.2 transcript_segments 表

字段：

- id
- meeting_id
- speaker
- start_time
- end_time
- raw_text
- clean_text
- created_at

---

### 6.3 meeting_summaries 表

字段：

- id
- meeting_id
- summary_json
- summary_markdown
- model_name
- prompt_version
- created_at

---

### 6.4 meeting_tasks 表

字段：

- id
- meeting_id
- owner
- task
- deadline
- status
- source_time
- created_at

---

## 7. 后端 API 设计

### 7.1 上传音频

POST /api/meetings

请求：

    multipart/form-data
    file: audio_file
    template_type: project_meeting

响应：

    {
      "meeting_id": "xxx",
      "status": "uploaded"
    }

---

### 7.2 查询处理状态

GET /api/meetings/{meeting_id}/status

响应：

    {
      "meeting_id": "xxx",
      "status": "transcribing",
      "progress": 45
    }

---

### 7.3 获取转写全文

GET /api/meetings/{meeting_id}/transcript

响应：

    {
      "segments": [
        {
          "speaker": "SPEAKER_00",
          "start": "00:00:03",
          "end": "00:00:08",
          "text": "我们今天主要讨论一下接口设计。"
        }
      ]
    }

---

### 7.4 获取会议纪要

GET /api/meetings/{meeting_id}/summary

响应：

    {
      "title": "...",
      "summary": "...",
      "decisions": [],
      "todos": [],
      "risks": [],
      "open_questions": []
    }

---

### 7.5 重新生成纪要

POST /api/meetings/{meeting_id}/regenerate

请求：

    {
      "template_type": "technical_review",
      "custom_instruction": "重点关注技术风险和待办事项"
    }

---

### 7.6 修改说话人姓名

POST /api/meetings/{meeting_id}/speakers

请求：

    {
      "SPEAKER_00": "赵健",
      "SPEAKER_01": "软件工程师"
    }

---

### 7.7 导出纪要

GET /api/meetings/{meeting_id}/export?format=md

format 可选：

- md
- docx
- pdf

---

## 8. 任务处理方式

上传音频后，不建议同步等待处理完成。

推荐异步任务流程：

用户上传音频
  ↓
后端创建 meeting
  ↓
返回 meeting_id
  ↓
后台任务开始处理
  ↓
前端轮询状态
  ↓
处理完成后展示结果

任务状态流转：

uploaded
  ↓
processing_audio
  ↓
transcribing
  ↓
diarizing
  ↓
cleaning_text
  ↓
summarizing
  ↓
completed

失败状态：

failed

失败时记录：

- failed_step
- error_message
- retry_count

---

## 9. 部署方案

### 9.1 MVP 云端方案

前端：
- Web 页面
- Vue / React

后端：
- FastAPI
- PostgreSQL / SQLite
- 本地文件存储

AI 能力：
- 云 ASR
- 云 LLM

优点：
- 开发最快
- 效果最好
- 适合验证产品价值

---

### 9.2 本地化方案

前端：
- RK3399 / 普通 Web 页面

后端：
- RK3588
- FastAPI gateway
- 本地文件存储
- 本地 ASR
- 本地 LLM 或局域网 LLM

优点：
- 数据不出本地
- 适合企业内网和隐私场景

缺点：
- 部署复杂
- 模型性能有限
- 需要优化推理速度

---

### 9.3 混合方案

本地：
- 音频上传
- 文件管理
- 任务调度
- 结果保存

云端：
- ASR
- LLM 总结

优点：
- 开发难度适中
- 效果较好
- 后续可切换成本地模型

---

## 10. 第一版 MVP 范围

第一版只做这些：

- 上传音频文件
- 自动转写全文
- 自动生成会议纪要
- 自动提取待办事项
- 支持说话人手动改名
- 支持 Markdown 导出
- 支持点击时间戳回听

第一版暂不做：

- 实时录音
- 实时转写
- 自动声纹识别真实姓名
- 多人协同编辑
- 知识库自动沉淀
- 钉钉 / 飞书任务同步
- 企业权限系统

---

## 11. 推荐开发顺序

### 阶段 1：跑通主流程

目标：

上传音频后，能生成一份完整纪要。

任务：

- 文件上传接口
- ffmpeg 音频转换
- ASR 转写
- LLM 总结
- 结果保存
- 前端展示

---

### 阶段 2：提升可用性

目标：

让用户觉得结果可信、可修改。

任务：

- 时间戳回听
- 说话人改名
- 转写文本编辑
- 纪要重新生成
- Markdown 导出

---

### 阶段 3：结构化增强

目标：

从“摘要工具”变成“会议管理工具”。

任务：

- 待办事项结构化
- 决策事项结构化
- 风险问题结构化
- 未决问题结构化
- 支持不同会议模板

---

### 阶段 4：私有化和知识库

目标：

适合公司内部长期使用。

任务：

- 项目词库
- 热词表
- 人员名单
- 历史会议检索
- 会议内容进入知识库
- 本地 ASR / 本地 LLM 适配

---

## 12. 关键技术风险

### 12.1 ASR 准确率

风险：

- 专业词识别错误
- 人名识别错误
- 中英文混说识别错误

解决：

- 加热词表
- 加项目词库
- 支持人工修正
- 保存修正结果反哺词库

---

### 12.2 说话人区分不准

风险：

- 多人声音相近
- 抢话
- 远程会议音质差

解决：

- 第一版只显示 SPEAKER_00
- 支持人工改名
- 不强依赖真实姓名识别

---

### 12.3 AI 纪要幻觉

风险：

- 模型编造不存在的结论
- 错误归属负责人
- 错误生成截止日期

解决：

- 每条结论绑定 source_time
- 提示词要求不得编造
- 没有明确负责人时写“未明确”
- 没有明确截止时间时写“未明确”
- 支持点击原文核对

---

### 12.4 长音频处理慢

风险：

- 1 小时以上音频处理时间长
- 用户等待体验差

解决：

- 异步任务
- 进度条
- 分段转写
- 分段总结
- 失败重试

---

## 13. 你这个项目的推荐架构

RK3399：

- 前端页面
- 文件上传
- 会议列表
- 纪要展示
- 音频回放

RK3588：

- FastAPI gateway
- 音频处理
- ASR 调用
- LLM 调用
- 结构化纪要生成
- 本地数据库
- 本地文件存储

整体结构：

RK3399 前端
  ↓
HTTP API
  ↓
RK3588 Gateway
  ↓
音频处理服务
  ↓
ASR 服务
  ↓
LLM 总结服务
  ↓
数据库 / 文件存储
  ↓
前端展示结果

---

## 14. 最小可行版本结论

第一版不要复刻钉钉完整 AI 听记。

先做：

上传音频文件
  ↓
转写全文
  ↓
AI 结构化纪要
  ↓
待办事项提取
  ↓
可编辑
  ↓
可导出

核心判断标准：

- 转写能不能看
- 纪要有没有用
- 待办提取准不准
- 用户能不能快速改
- 结果能不能追溯到原文

只要这五点跑通，就已经是一个可用的 AI 会议纪要 MVP。