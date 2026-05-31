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
- 画面处理预设：轻度、标准、强处理、自定义。
- 画面参数：亮度、对比度、饱和度、锐化、降噪、分辨率、裁切/拉伸/背景模糊、翻转、轻微旋转、帧率、抽帧、动态缩放、固定码率。
- `/effects/filter-graph` 可把处理参数转换为 `ffmpeg` filter graph。
- `/effects/preview` 可生成短片段预览。
- `/effects/apply` 可执行完整画面处理任务。
- 字幕预设支持语言、单/双行、字体、字号、颜色、描边、阴影、背景透明度和实时预览。
- 配音配置内置配音 API 管理、音色目录、试听、语速、音量、音调、输出格式、采样率、码率和风格提示。

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
