# YouTube 视频处理桌面软件计划

## Summary
构建一个 Windows 桌面端软件：前端使用 `React + TypeScript + Vite`，桌面壳使用 `Electron`，后端使用 `Python + FastAPI`。核心流程是：

`解析 YouTube 视频 -> 下载入库 -> 画面处理/视频增强 -> 添加字幕 -> 可选配音 -> 合成导出`

后端负责 `yt-dlp`、`ffmpeg`、字幕处理、API 调用、配音、导出和一键流程编排；前端负责任务队列、视频预览、字幕样式配置、API 配置和日志展示。

## Key Changes / Implementation
- 桌面架构：
  - `Electron` 启动 React 前端，并作为桌面壳管理窗口、文件选择、托盘、打包。
  - `Electron main` 启动本地 Python FastAPI 服务。
  - 前端通过 HTTP/WebSocket 和 Python 后端通信。
  - 工具优先使用 `D:\tools\yt-dlp`、`D:\tools\ffmpeg`。

- 下载流水线：
  - 输入 YouTube URL 后调用 Python 后端解析视频信息。
  - 展示标题、作者、时长、清晰度、字幕轨、缩略图。
  - 用户确认后下载视频，并自动创建后续处理任务。
  - 下载完成后进入画面处理、字幕、配音、导出队列。

- 画面处理/视频增强：
  - 用于视频差异化处理，不再命名为“自动去重”。
  - 支持亮度、对比度、饱和度、锐化、降噪、分辨率、画布、翻转、轻微旋转、帧率、抽帧、动态缩放和码率控制。
  - 支持轻度、标准、强处理、自定义预设，每个参数支持固定值或随机范围。
  - 参数保存后会被一键完成流程自动复用。

- 字幕系统：
  - 支持 YouTube 原字幕/自动字幕。
  - 支持通过文本 API 生成、翻译、润色字幕正文。
  - 文本 API 处理后的字幕会映射回原字幕时间轴，后续需要更精细的分段批处理和逐句对齐。
  - 字幕内容保存为 `SRT/VTT`，字幕样式保存为 `ASS`。
  - 字幕配置支持单行/双行、字体、字号、位置、颜色、描边、阴影、背景透明度。
  - 支持单独字幕颜色，例如按语言、说话人或关键词区分。
  - 默认导出硬字幕视频，同时保留字幕源文件。

- API 配置系统：
  - 文本 API 支持 `OpenAI Responses`、`OpenAI-compatible`、`Gemini native`、`Gemini OpenAI-compatible`、`Anthropic Messages`、自定义渠道。
  - 配音 API 支持 `OpenAI TTS`、`Gemini TTS`、自定义 TTS 渠道。
  - API 配置可保存、切换、测试连接。
  - `apiKey` 本地加密保存，日志禁止输出密钥。
  - API 适配参考官方文档：[OpenAI Responses](https://platform.openai.com/docs/api-reference/responses/create?api-mode=responses)、[OpenAI TTS](https://platform.openai.com/docs/guides/text-to-speech?lang=curl)、[Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)、[Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation)、[Anthropic Messages](https://docs.anthropic.com/en/api/messages-examples)。

- 配音系统：
  - 配音为可选步骤。
  - 支持从字幕文本、翻译文本或用户自定义文案生成配音。
  - 当前一键流程优先使用字幕正文生成整段配音；按字幕时间轴分段生成和对齐是后续增强。
  - 支持保留原声、降低原声音量、替换原声。
  - 生成音频后由 `ffmpeg` 合成到最终视频。

- 一键自动化：
  - `POST /automation/start` 创建后台自动化任务，立即返回 `job_id`。
  - `GET /automation/jobs` 和 `GET /automation/jobs/{id}` 展示自动化任务列表、阶段进度、错误和最终导出路径。
  - `POST /automation/run` 保留同步执行入口，方便脚本、测试和调试直接跑完整链路。
  - 如果已保存文本 API 配置，一键流程会默认对字幕正文做润色；没有文本配置时直接使用 YouTube 字幕。
  - 字幕失败、配音失败属于可跳过阶段，主流程会尽量继续导出。
  - 当前已是后台任务执行，后续要升级为可配置并发队列、断点恢复和 WebSocket/SSE 进度推送。

- UI/UX：
  - 第一屏直接进入工作台，不做营销页。
  - 顶部为 URL 输入、解析按钮、全局任务状态。
  - 左侧为任务队列、素材库、字幕预设、API 配置、配音配置、历史记录。
  - 中间为当前任务流程时间线。
  - 右侧为视频预览、字幕预览、样式面板。
  - 底部为日志、错误、重试和导出状态。
  - 所有按钮、配置项、复杂逻辑处的代码注释必须使用中文。

## Public Interfaces / Types
- 后端提供最小 API：
  - `POST /videos/parse`：解析 YouTube URL。
  - `POST /videos/download`：创建下载任务。
  - `POST /automation/start`：创建后台一键自动流程任务。
  - `GET /automation/jobs`：获取一键自动流程任务列表。
  - `GET /automation/jobs/{id}`：获取一键自动流程任务进度。
  - `POST /automation/run`：执行一键自动流程。
  - `GET /tasks`：获取任务列表。
  - `GET /tasks/{id}`：获取单个任务状态。
  - `POST /tasks/{id}/retry`：重试任务。
  - `POST /subtitles/render`：生成字幕文件或硬字幕视频。
  - `POST /subtitles/process-text`：使用文本 API 生成、翻译或润色字幕正文。
  - `POST /voice/generate`：生成配音。
  - `POST /exports/create`：创建导出任务。
  - `POST /profiles/test`：测试 API 配置。
  - `GET/POST/PUT/DELETE /profiles`：管理 API 配置。

- 核心数据类型：
  - `VideoSource`：平台、URL、video_id、标题、作者、时长。
  - `DownloadTask`：下载参数、状态、进度、输出文件。
  - `SubtitlePreset`：单双行、字体、颜色、描边、位置、透明度。
  - `TextProviderProfile`：文本模型渠道配置。
  - `VoiceProviderProfile`：配音渠道配置。
  - `ExportJob`：输入视频、字幕、音频、导出格式、输出路径。

## Test Plan
- 下载测试：
  - 单个 YouTube URL 可解析、下载、入库。
  - 重复 URL 会被识别并跳过。
  - 下载失败时显示明确错误，并支持重试。

- 自动化测试：
  - `/automation/start` 可创建后台自动化任务，前端任务列表能轮询显示进度。
  - `/automation/run` 可按顺序创建下载、画面处理、字幕、配音、导出任务。
  - 没有字幕或配音配置时对应阶段可跳过，导出仍继续。
  - 最终响应返回导出文件路径和每个阶段状态。

- 字幕测试：
  - YouTube 字幕可抓取并保存。
  - 字幕样式配置能正确生成 `ASS`。
  - 文本 API 可对字幕正文做生成、翻译、润色，错误不泄露 key。
  - 单行/双行、颜色、描边、位置在预览和导出中一致。
  - 硬字幕导出后视频可正常播放。

- API 测试：
  - OpenAI-compatible、Gemini、Anthropic 文本配置可保存和切换。
  - 配置测试失败时展示原因，不泄露 key。
  - 配音配置可生成音频并参与合成。

- 桌面测试：
  - Electron 能启动 Python 后端。
  - 前端能实时显示任务进度和日志。
  - 打包后的 Windows 软件能在无开发环境下运行。
  - `D:\tools` 下已有工具时优先使用；不存在时提示安装或引导下载。

## Assumptions / Defaults
- 默认桌面壳使用 `Electron`。
- 默认前端使用 `React + TypeScript + Vite`。
- 默认后端使用 `Python + FastAPI`。
- 默认数据库使用 `SQLite`。
- 默认视频处理使用 `yt-dlp + ffmpeg`，参考：[yt-dlp README](https://github.com/yt-dlp/yt-dlp/blob/master/README.md)、[FFmpeg 文档](https://ffmpeg.org/documentation.html?g=drawtext&group=filtering)。
- 默认导出硬字幕视频，同时保留 `SRT/ASS` 文件。
- 默认先做 Windows 版，后续再考虑 macOS/Linux。
- 默认 API 渠道允许自定义 `baseUrl`，用于兼容非官方中转或第三方服务。
- 默认所有新增代码注释使用中文。
