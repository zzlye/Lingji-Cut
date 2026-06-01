// src/lib/api.ts
// API 客户端 - 封装与后端的 HTTP 通信

/** 后端基础地址 */
const BASE_URL = 'http://127.0.0.1:8765'

/**
 * 通用请求方法
 * @param path API 路径
 * @param options 请求选项
 * @returns 响应数据
 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json()
}

/** 视频 API */
export const videoApi = {
  /** 解析 YouTube 视频 */
  parse: (url: string) =>
    request<import('@/types').VideoParseResult>('/videos/parse', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  /** 创建下载任务 */
  download: (videoId: number, formatId?: string) =>
    request<{ message: string; task_id: number; output_path?: string }>('/videos/download', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId, format_id: formatId }),
    }),
}

/** 画面处理 API */
export const effectsApi = {
  /** 获取画面处理预设 */
  listPresets: () =>
    request<import('@/types').ProcessingPreset[]>('/effects/presets'),

  /** 保存画面处理预设 */
  createPreset: (preset: {
    name: string
    intensity: 'light' | 'standard' | 'strong' | 'custom'
    is_default?: boolean
    config: import('@/types').ProcessingConfig
  }) =>
    request<import('@/types').ProcessingPreset>('/effects/presets', {
      method: 'POST',
      body: JSON.stringify(preset),
    }),

  /** 删除画面处理预设 */
  deletePreset: (id: number) =>
    request<{ message: string }>(`/effects/presets/${id}`, { method: 'DELETE' }),

  /** 生成 ffmpeg 滤镜字符串 */
  buildFilterGraph: (preset: import('@/types').ProcessingConfig) =>
    request<{ filter_graph: string }>('/effects/filter-graph', {
      method: 'POST',
      body: JSON.stringify({ preset }),
    }),

  /** 生成预览片段 */
  preview: (params: {
    video_path: string
    preset: import('@/types').ProcessingConfig
    start_time?: number
    duration?: number
    output_path?: string
  }) =>
    request<{ message: string; output_path: string; filter_graph: string }>('/effects/preview', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 执行完整画面处理 */
  apply: (params: {
    video_path: string
    preset: import('@/types').ProcessingConfig
    output_path?: string
  }) =>
    request<{ message: string; output_path: string; filter_graph: string; task_id?: number }>('/effects/apply', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

/** 任务 API */
export const taskApi = {
  /** 获取任务列表 */
  list: (status?: string) =>
    request<import('@/types').DownloadTask[]>(`/tasks${status ? `?status=${status}` : ''}`),

  /** 获取单个任务 */
  get: (id: number) =>
    request<import('@/types').DownloadTask>(`/tasks/${id}`),

  /** 重试任务 */
  retry: (id: number) =>
    request<{ message: string }>(`/tasks/${id}/retry`, { method: 'POST' }),

  /** 暂停底层任务并停止当前外部进程 */
  pause: (id: number) =>
    request<{ message: string; task_id: number; killed_count: number }>(`/tasks/${id}/pause`, { method: 'POST' }),

  /** 取消底层任务并停止当前外部进程 */
  cancel: (id: number) =>
    request<{ message: string; task_id: number; killed_count: number }>(`/tasks/${id}/cancel`, { method: 'POST' }),

  /** 删除单条底层任务记录 */
  delete: (id: number, force = false) =>
    request<{ message: string; task_id: number }>(`/tasks/${id}${force ? '?force=true' : ''}`, { method: 'DELETE' }),

  /** 批量清理底层任务记录 */
  clear: (status?: import('@/types').DownloadTask['status']) =>
    request<{ message: string; deleted_count: number }>(`/tasks${status ? `?status=${status}` : ''}`, { method: 'DELETE' }),

  /** 将卡住的执行中任务标记失败，随后可删除或重试 */
  cleanupInterrupted: () =>
    request<{ message: string; updated_count: number }>('/tasks/cleanup-interrupted', { method: 'POST' }),
}

/** 字幕 API */
export const subtitleApi = {
  /** 获取字幕预设列表 */
  listPresets: () =>
    request<import('@/types').SubtitlePreset[]>('/subtitles/presets'),

  /** 创建字幕预设 */
  createPreset: (preset: Partial<import('@/types').SubtitlePreset>) =>
    request<import('@/types').SubtitlePreset>('/subtitles/presets', {
      method: 'POST',
      body: JSON.stringify(preset),
    }),

  /** 更新字幕预设 */
  updatePreset: (id: number, preset: Partial<import('@/types').SubtitlePreset>) =>
    request<import('@/types').SubtitlePreset>(`/subtitles/presets/${id}`, {
      method: 'PUT',
      body: JSON.stringify(preset),
    }),

  /** 删除字幕预设 */
  deletePreset: (id: number) =>
    request<{ message: string }>(`/subtitles/presets/${id}`, { method: 'DELETE' }),

  /** 下载/生成字幕文件并可烧录硬字幕 */
  render: (params: {
    video_id: number
    video_path: string
    preset_id?: number
    language?: string
    sub_type?: 'original' | 'auto'
    burn_in?: boolean
    subtitle_path?: string
    output_path?: string
  }) =>
    request<{ message: string; task_id: number; subtitle_path: string; ass_path: string; output_path?: string | null; plain_text: string }>('/subtitles/render', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 使用文本 API 生成、翻译或润色字幕正文 */
  processText: (params: {
    text: string
    profile_id: number
    operation?: 'generate' | 'translate' | 'polish'
    target_language?: string
  }) =>
    request<{ message: string; text: string; operation: string }>('/subtitles/process-text', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 读取本地字幕文件并转换成可编辑条目 */
  parseFile: (subtitlePath: string) =>
    request<{ message: string; entries: import('@/types').SubtitleEntry[]; plain_text: string; output_path?: string; format?: string }>('/subtitles/parse-file', {
      method: 'POST',
      body: JSON.stringify({ subtitle_path: subtitlePath }),
    }),

  /** 解析粘贴的 SRT/VTT 字幕文本 */
  parseText: (params: { content: string; format: 'srt' | 'vtt' }) =>
    request<{ message: string; entries: import('@/types').SubtitleEntry[]; plain_text: string; output_path?: string; format?: string }>('/subtitles/parse-text', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 保存手动校对后的 SRT 文件 */
  saveCorrected: (params: { entries: import('@/types').SubtitleEntry[]; output_path?: string; file_name?: string; format?: 'srt' }) =>
    request<{ message: string; entries: import('@/types').SubtitleEntry[]; plain_text: string; output_path: string; format: string }>('/subtitles/save', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 按字幕预设生成 ASS 文件 */
  saveAss: (params: { entries: import('@/types').SubtitleEntry[]; output_path?: string; file_name?: string; preset_id?: number | null }) =>
    request<{ message: string; entries: import('@/types').SubtitleEntry[]; plain_text: string; output_path: string; format: string }>('/subtitles/save-ass', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

/** API 配置 */
export const profileApi = {
  /** 获取文本 API 配置 */
  listText: () =>
    request<import('@/types').ApiProfile[]>('/profiles/text'),

  /** 创建文本 API 配置 */
  createText: (profile: { name: string; provider_type: string; base_url: string; api_key: string; model?: string; extra_params?: string }) =>
    request<import('@/types').ApiProfile>('/profiles/text', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),

  /** 更新文本 API 配置 */
  updateText: (id: number, profile: { name: string; provider_type: string; base_url: string; api_key?: string; model?: string; extra_params?: string }) =>
    request<import('@/types').ApiProfile>(`/profiles/text/${id}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  /** 获取文本模型列表 */
  listTextModels: (params: { provider_type: string; base_url: string; api_key?: string; profile_id?: number | null }) =>
    request<{ models: import('@/types').TextModelOption[]; source: string; message: string }>('/profiles/text/models', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 获取配音 API 配置 */
  listVoice: () =>
    request<import('@/types').ApiProfile[]>('/profiles/voice'),

  /** 创建配音 API 配置 */
  createVoice: (profile: { name: string; provider_type: string; base_url: string; api_key: string; model?: string; extra_params?: string }) =>
    request<import('@/types').ApiProfile>('/profiles/voice', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),

  /** 更新配音 API 配置 */
  updateVoice: (id: number, profile: { name: string; provider_type: string; base_url: string; api_key?: string; model?: string; extra_params?: string }) =>
    request<import('@/types').ApiProfile>(`/profiles/voice/${id}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  /** 获取配音模型列表 */
  listVoiceModels: (params: { provider_type: string; base_url: string; api_key?: string; profile_id?: number | null }) =>
    request<{ models: import('@/types').TextModelOption[]; source: string; message: string }>('/profiles/voice/models', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 测试当前配音表单，支持未保存配置 */
  testVoiceForm: (params: {
    name: string
    provider_type: string
    base_url: string
    api_key?: string
    model?: string
    extra_params?: string
    profile_id?: number | null
  }) =>
    request<{ message: string; status: string }>('/profiles/voice/test', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 测试配置连接 */
  test: (type: string, id: number) =>
    request<{ message: string; status: string }>(`/profiles/test/${type}/${id}`, { method: 'POST' }),
}

/** 配音 API */
export const voiceApi = {
  /** 获取音色目录 */
  voices: (providerType: string) =>
    request<{ voices: import('@/types').VoiceOption[] }>('/voice/voices', {
      method: 'POST',
      body: JSON.stringify({ provider_type: providerType }),
    }),

  /** 生成配音 */
  generate: (params: {
    text: string
    profile_id: number
    voice?: string
    model?: string
    settings?: Partial<import('@/types').VoiceGenerateSettings>
    output_path?: string
  }) =>
    request<{ message: string; output_path: string; audio_url: string }>('/voice/generate', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 当前表单直接试听，支持未保存配置 */
  preview: (params: {
    text: string
    profile_id?: number | null
    provider_type: string
    base_url: string
    api_key?: string
    voice?: string
    model?: string
    settings?: Partial<import('@/types').VoiceGenerateSettings>
  }) =>
    request<{ message: string; output_path: string; audio_url: string }>('/voice/preview', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

/** 导出 API */
export const exportApi = {
  /** 创建导出任务 */
  create: (params: {
    video_path: string
    subtitle_path?: string
    audio_path?: string
    output_format?: string
    audio_mode?: 'replace' | 'mix'
    original_volume?: number
  }) =>
    request<{ message: string; task_id: number; output_path: string }>('/exports/create', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
}

/** 一键自动化 API */
export type AutomationStartParams = {
  url: string
  enable_effects?: boolean
  processing_preset: import('@/types').ProcessingConfig
  format_id?: string
  output_format?: string
  subtitle_preset_id?: number
  subtitle_language?: string
  text_profile_id?: number
  subtitle_operation?: 'none' | 'generate' | 'translate' | 'polish'
  subtitle_target_language?: string
  burn_subtitles?: boolean
  enable_voice?: boolean
  voice_profile_id?: number
  voice_text?: string
  voice_mode?: 'full' | 'segmented'
  audio_mode?: 'replace' | 'mix'
  original_volume?: number
  multi_speaker_enabled?: boolean
  speaker_voice_map?: Record<string, string>
  glossary_terms?: import('@/types').GlossaryTerm[]
  banned_words?: string[]
  banned_word_action?: 'warn' | 'block'
}

export const automationApi = {
  /** 后端完整执行一键流程 */
  run: (params: AutomationStartParams) =>
    request<import('@/types').AutomationRunResponse>('/automation/run', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 启动后台一键流程，立即返回任务 ID */
  start: (params: AutomationStartParams) =>
    request<import('@/types').AutomationStartResponse>('/automation/start', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 批量启动后台一键流程 */
  startBatch: (params: { urls: string[]; template: AutomationStartParams; concurrency?: number }) =>
    request<import('@/types').AutomationBatchStartResponse>('/automation/batch/start', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** 暂停批量流程中还没开始的任务 */
  pauseBatch: (batchId: string) =>
    request<import('@/types').AutomationBatchControlResponse>(`/automation/batch/${batchId}/pause`, { method: 'POST' }),

  /** 恢复批量流程中暂停的任务 */
  resumeBatch: (batchId: string) =>
    request<import('@/types').AutomationBatchControlResponse>(`/automation/batch/${batchId}/resume`, { method: 'POST' }),

  /** 获取后台一键流程列表 */
  listJobs: () =>
    request<import('@/types').BackendAutomationJob[]>('/automation/jobs'),

  /** 获取后台一键流程进度 */
  getJob: (id: string) =>
    request<import('@/types').BackendAutomationJob>(`/automation/jobs/${id}`),

  /** 重试后台一键流程 */
  retry: (id: string) =>
    request<import('@/types').AutomationStartResponse>(`/automation/jobs/${id}/retry`, { method: 'POST' }),

  /** 从已有阶段继续后台一键流程 */
  resume: (id: string) =>
    request<import('@/types').AutomationStartResponse>(`/automation/jobs/${id}/resume`, { method: 'POST' }),

  /** 暂停后台一键流程，并停止当前外部进程 */
  pause: (id: string) =>
    request<import('@/types').AutomationStartResponse>(`/automation/jobs/${id}/pause`, { method: 'POST' }),

  /** 取消后台一键流程，并停止当前外部进程 */
  cancel: (id: string) =>
    request<import('@/types').AutomationStartResponse>(`/automation/jobs/${id}/cancel`, { method: 'POST' }),

  /** 跳过当前自动化阶段，先支持耗时画面处理阶段 */
  skipCurrentStage: (id: string) =>
    request<import('@/types').AutomationStartResponse>(`/automation/jobs/${id}/skip-current-stage`, { method: 'POST' }),

  /** 后台一键流程事件流地址 */
  eventsUrl: (id: string) =>
    `${BASE_URL}/automation/jobs/${id}/events`,
}

/** 设置 API */
export const settingsApi = {
  /** 获取项目文件夹 */
  paths: () =>
    request<import('@/types').ProjectPaths>('/settings/paths'),

  /** 更新项目文件夹 */
  updatePaths: (projectRoot: string) =>
    request<import('@/types').ProjectPaths>('/settings/paths', {
      method: 'PUT',
      body: JSON.stringify({ project_root: projectRoot }),
    }),

  /** 恢复默认项目文件夹 */
  resetPaths: () =>
    request<import('@/types').ProjectPaths>('/settings/paths/reset', { method: 'POST' }),

  /** 获取自动化依赖工具状态 */
  tools: () =>
    request<import('@/types').ToolStatusMap>('/settings/tools'),
}
