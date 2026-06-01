# YouTube 视频处理器

YouTube 视频下载、画面处理、字幕、配音、导出处理桌面软件。

## 技术栈

- **前端**：React + TypeScript + Vite + Tailwind CSS v4 + shadcn/ui
- **桌面壳**：Electron + electron-vite
- **后端**：Python + FastAPI
- **数据库**：SQLite + SQLAlchemy

## 项目结构

```
├── electron/                # Electron 主进程和预加载脚本
│   ├── main/index.ts        # 主进程入口
│   ├── preload/index.ts     # 预加载脚本
│   └── shared/types.ts      # 共享类型
├── src/                     # React 前端
│   ├── components/layout/   # 布局组件（AppShell、Header、Sidebar、LogPanel）
│   ├── styles/globals.css   # 全局样式（暗色主题）
│   ├── App.tsx              # 根组件
│   └── main.tsx             # React 入口
├── backend/                 # Python FastAPI 后端
│   ├── api/                 # API 路由
│   ├── models/              # 数据库模型
│   ├── utils/               # 工具函数
│   ├── main.py              # FastAPI 应用入口
│   └── run.py               # 嵌入式 Python 启动器
└── data/                    # 数据库文件目录
```

## 已实现能力

- YouTube URL 解析和下载任务接口。
- 一键自动流程已由后端自动化任务编排：`解析 -> 下载 -> 画面处理 -> 字幕渲染 -> 可选配音 -> 导出`。
  - `/automation/start` 会创建后台任务并立即返回 `job_id`，前端优先通过 `/automation/jobs/{id}/events` 接收 SSE 实时进度，异常时降级为 `/automation/jobs/{id}` 查询。
  - `/automation/batch/start` 支持一次提交多个 YouTube 链接，按用户设置的批次并发数排队执行。
  - `/automation/jobs/{id}/retry` 支持失败或已完成任务按原参数重新进入队列。
  - `/automation/jobs/{id}/resume` 支持断点续跑，会复用已完成且文件仍存在的下载、画面处理、字幕和配音阶段。
  - `/automation/batch/{batch_id}/pause` 和 `/automation/batch/{batch_id}/resume` 支持批次暂停/恢复，暂停会阻止后续待调度任务继续执行。
  - `/automation/run` 保留同步执行入口，方便脚本和测试直接跑完整链路。
  - 字幕步骤会自动选择解析到的字幕轨，并使用默认/首个字幕预设生成 `ASS`，同时尝试烧录硬字幕。
  - 配音为可选步骤：存在已保存配音配置时优先按字幕时间轴分段生成并对齐配音，失败时回退整段配音；没有配置或生成失败时跳过并继续导出。
  - 导出步骤会生成最终 `mp4` 到项目 `exports` 目录。
- 画面处理预设：轻度、标准、强处理、自定义。
- 画面参数：亮度、对比度、饱和度、锐化、降噪、分辨率、裁切/拉伸/背景模糊、翻转、轻微旋转、帧率、抽帧、动态缩放、固定码率。
- `/effects/filter-graph` 可把处理参数转换为 `ffmpeg` filter graph。
- `/effects/preview` 可生成短片段预览。
- `/effects/apply` 可执行完整画面处理任务。
- `/subtitles/render` 可下载 YouTube 字幕、生成 `ASS`，并可烧录硬字幕视频。
- `/subtitles/process-text` 可使用已保存文本 API 生成、翻译或润色字幕正文。
- `/automation/start`、`/automation/batch/start`、`/automation/batch/{batch_id}/pause`、`/automation/batch/{batch_id}/resume`、`/automation/jobs`、`/automation/jobs/{id}`、`/automation/jobs/{id}/events`、`/automation/jobs/{id}/retry`、`/automation/jobs/{id}/resume` 可创建、批量入队、批次暂停/恢复、列表展示、实时推送、查询、重试和断点续跑后台自动化任务。
- `/automation/run` 可同步执行完整一键流程，并返回每个阶段的任务状态和最终导出路径。
- 字幕预设支持语言、单/双行、字体、字号、九宫格位置、颜色、描边、阴影、背景透明度和实时预览。
- 配音配置内置配音 API 管理、音色目录、试听、语速、音量、音调、输出格式、采样率、码率和风格提示。
- `/exports/create` 可执行字幕烧录、音频合成和最终格式导出。

## 自动化状态

当前一键流程已经具备闭环，但还有这些产品化限制：

- 字幕内容优先来自 YouTube 原字幕/自动字幕；存在已保存文本 API 配置时，一键流程会默认对字幕正文做润色。
- 文本 API 已支持生成、翻译、润色入口；一键流程会把处理后的文本映射回原字幕时间轴，后续还需要做更精细的分段批处理和逐句对齐。
- 自动配音当前优先使用字幕时间轴分段生成并通过 ffmpeg 对齐为完整音轨；字幕缺失或分段失败时回退整段配音或标题文案。
- 一键流程已沉到后端后台任务编排，并支持批量入队、批次暂停/恢复、SSE 实时进度、任务重试和断点续跑；后续还需要更细的优先级、定时和失败策略控制。

## 配音渠道支持

配音参数按各家文档映射到对应 API：

- **OpenAI TTS / OpenAI-compatible**：`voice`、`response_format`、`speed`、`instructions`。
- **Gemini TTS**：`speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`，风格和语速主要通过提示词控制。
- **MiniMax T2A**：`voice_setting.voice_id/speed/vol/pitch/emotion`、`audio_setting.format/sample_rate/bitrate/channel`、`voice_modify.intensity/timbre/sound_effects`。
- **小米 MiMo TTS**：OpenAI 风格 `chat/completions`，使用 `modalities: ["text", "audio"]` 和 `audio.voice/audio.format`。
- **自定义 TTS**：按 OpenAI `/audio/speech` 兼容接口处理，保留自定义 Base URL、模型和 voice id。

## 快速开始

### 安装依赖

```bash
# Node.js 依赖
npm install

# Python 依赖（推荐安装到 D:\tools 的嵌入式 Python）
D:\tools\python-3.12.10-embed\python.exe -m pip install -r backend\requirements.txt
```

### 启动开发

```bash
# 启动 Electron + React 开发服务器
npm run dev
```

Electron 主进程会优先使用：

```text
D:\tools\python-3.12.10-embed\python.exe
```

并通过 `backend/run.py` 启动 FastAPI 后端。

### 单独验证后端

```powershell
D:\tools\python-3.12.10-embed\python.exe backend\run.py
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/effects/presets
```

### 本地自动化测试

```powershell
D:\tools\python-3.12.10-embed\python.exe backend\tests\test_subtitle_mapping.py -v
D:\tools\python-3.12.10-embed\python.exe backend\tests\test_local_media_pipeline.py -v
D:\tools\python-3.12.10-embed\python.exe backend\tests\test_automation_jobs.py -v
```

`test_local_media_pipeline.py` 会用本地 ffmpeg 验证画面处理、字幕烧录、导出和分段配音混合，不依赖外部 YouTube 或真实 TTS API。

## 外部工具路径

后端优先查找：

```text
D:\tools\yt-dlp\yt-dlp.exe
D:\tools\ffmpeg\ffmpeg.exe
```

如果不存在，会回退到系统 `PATH` 中的 `yt-dlp` 和 `ffmpeg`。

### 构建打包

```bash
# 构建生产版本
npm run build

# 打包 Windows 安装包
npm run build:win
```

## 设计系统

- **风格**：Dark Mode (OLED)
- **主色**：#EC4899（粉红）
- **强调色**：#2563EB（蓝）
- **背景**：#0F172A
- **字体**：Inter + JetBrains Mono
