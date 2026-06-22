// src/types/index.ts
// TypeScript 类型定义 - 前后端共享的数据类型

/** 视频源信息 */
export interface VideoSource {
  id: number
  platform: string
  video_id: string
  url: string
  title: string | null
  author: string | null
  duration: number | null
  thumbnail_url: string | null
  formats: string | null
  subtitles: string | null
  created_at: string
}

/** 视频解析响应 */
export interface VideoParseResult {
  id: number
  video_id: string
  platform: string
  title: string | null
  author: string | null
  duration: number | null
  thumbnail_url: string | null
  cover_asset_path?: string | null
  formats: VideoFormat[]
  subtitles: SubtitleTrack[]
  format_count?: number
  subtitle_count?: number
}

/** 视频格式 */
export interface VideoFormat {
  format_id: string
  resolution: string
  ext: string
  fps: number
  filesize: number
  vcodec: string
  acodec: string
}

/** 字幕轨 */
export interface SubtitleTrack {
  language: string
  name: string
  ext: string
  type: 'original' | 'auto'
}

/** 可手动校对的字幕条目 */
export interface SubtitleEntry {
  index: number
  start: string
  end: string
  text: string
}

/** 下载任务 */
export interface DownloadTask {
  id: number
  video_id: number
  task_type: 'download' | 'effects' | 'subtitle' | 'voice' | 'export'
  status: 'pending' | 'downloading' | 'processing' | 'paused' | 'cancelled' | 'completed' | 'failed' | 'skipped'
  progress: number
  output_path: string | null
  error_message: string | null
  created_at: string
  completed_at: string | null
  can_pause?: boolean
  can_cancel?: boolean
  can_retry?: boolean
  can_delete?: boolean
}

/** 字幕预设 */
export interface SubtitlePreset {
  id: number
  name: string
  is_default: boolean
  line_mode: 'single' | 'double'
  language: string
  font_name: string
  font_size: number
  secondary_font_size: number
  font_color: string
  secondary_color: string
  outline_color: string
  outline_width: number
  shadow_enabled: boolean
  shadow_color: string
  shadow_x: number
  shadow_y: number
  background_alpha: number
  position: 'top_left' | 'top' | 'top_right' | 'middle_left' | 'center' | 'middle_right' | 'bottom_left' | 'bottom' | 'bottom_right'
  margin_v: number
}

/** 路径信息 */
export interface PathInfo {
  path: string
  exists: boolean
}

/** 项目文件夹设置 */
export interface ProjectPaths {
  project_root: PathInfo
  default_project_root: PathInfo
  videos_dir: PathInfo
  data_dir: PathInfo
  downloads_dir: PathInfo
  output_dir: PathInfo
  exports_dir: PathInfo
}

/** YouTube 登录 Cookies 设置 */
export interface YtdlpCookieSettings {
  cookies_file: string
  cookies_browser: string
  cookies_file_exists: boolean
}

/** 外部工具状态 */
export interface ToolStatus {
  name: string
  command: string
  available: boolean
  version: string | null
  source: string
  error_message: string | null
}

/** 自动化依赖工具状态 */
export interface ToolStatusMap {
  yt_dlp: ToolStatus
  ffmpeg: ToolStatus
}

/** API 配置 */
export interface ApiProfile {
  id: number
  name: string
  provider_type: string
  base_url: string
  model: string | null
  extra_params?: string | null
}

/** 文本 API 生成和自动化参数 */
export interface TextApiSettings {
  temperature: number
  top_p: number
  top_k: number
  max_tokens: number
  concurrency: number
  timeout_seconds: number
  retry_count: number
  retry_interval_ms: number
  rate_limit_rpm: number
  subtitle_batch_size: number
  subtitle_batch_chars: number
  /** 旧版兼容字段；新提示词预设已独立保存 */
  system_prompt?: string
  response_format: 'text' | 'json'
  stream: boolean
}

/** 文本模型选项 */
export interface TextModelOption {
  id: string
  label: string
  owned_by?: string | null
}

/** 文本 API 提示词预设，独立于 API 渠道配置 */
export interface TextPromptPreset {
  id: string
  name: string
  prompt: string
  description: string
}

/** 自动处理流程阶段 */
export interface AutomationStep {
  key: 'parse' | 'download' | 'effects' | 'subtitle' | 'voice' | 'export'
  label: string
  description: string
  status: 'pending' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed' | 'skipped'
  progress: number
  output_path?: string | null
  error_message?: string | null
}

/** 自动处理流程任务 */
export interface AutomationJob {
  id: string
  title: string
  source_url: string
  video_id: number | null
  video_info?: VideoParseResult | null
  status: 'pending' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed'
  progress: number
  current_step: string
  error_message?: string | null
  batch_id: string | null
  created_at: string
  completed_at: string | null
  steps: AutomationStep[]
  can_pause?: boolean
  can_cancel?: boolean
  can_resume?: boolean
  can_retry?: boolean
  subtitle_asset_path?: string | null
  source_subtitle_path?: string | null
  translated_subtitle_path?: string | null
  source_video_path?: string | null
  voice_asset_path?: string | null
  subtitle_only_video_path?: string | null
  cover_asset_path?: string | null
  output_path?: string | null
}

/** 后端持久化自动化任务 */
export interface BackendAutomationJob {
  id: string
  source_url: string
  video_id: number | null
  video_info?: VideoParseResult | null
  title: string | null
  status: 'pending' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed'
  progress: number
  current_step: string | null
  output_path: string | null
  error_message: string | null
  batch_id: string | null
  stages: AutomationStageResult[]
  subtitle_text: string
  created_at: string | null
  completed_at: string | null
  can_pause?: boolean
  can_cancel?: boolean
  can_resume?: boolean
  can_retry?: boolean
  subtitle_asset_path?: string | null
  source_subtitle_path?: string | null
  translated_subtitle_path?: string | null
  source_video_path?: string | null
  voice_asset_path?: string | null
  subtitle_only_video_path?: string | null
  cover_asset_path?: string | null
}

/** 后端自动化阶段结果 */
export interface AutomationStageResult {
  key: AutomationStep['key'] | 'pipeline'
  status: 'pending' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed' | 'skipped'
  progress?: number
  task_id?: number | null
  output_path?: string | null
  error_message?: string | null
}

/** 后端一键流程响应 */
export interface AutomationRunResponse {
  message: string
  video_id: number
  title: string | null
  output_path: string
  subtitle_only_video_path?: string | null
  stages: AutomationStageResult[]
  subtitle_text: string
}

/** 后端启动自动化任务响应 */
export interface AutomationStartResponse {
  message: string
  job_id: string
}

/** 后端重新合成导出响应 */
export interface AutomationReExportResponse {
  message: string
  job_id: string
  task_id: number
  output_path: string
  subtitle_path?: string | null
  audio_path?: string | null
  video_path: string
}

/** 后端批量启动自动化任务响应 */
export interface AutomationBatchStartResponse {
  message: string
  batch_id: string
  job_ids: string[]
  accepted_count: number
  skipped_count: number
}

/** 后端批量流程控制响应 */
export interface AutomationBatchControlResponse {
  message: string
  batch_id: string
  affected_count: number
}

/** 自动化字幕文本处理方式 */
export type SubtitleTextOperation = 'skip' | 'none' | 'generate' | 'translate' | 'polish'

/** 字幕识别方式：本地 Whisper / Gemini 全片转写 / Gemini 内容+本地时间轴 */
export type SubtitleRecognitionMode = 'local' | 'gemini_full' | 'gemini_align'

/** 配音说话人音色配置 */
export interface VoiceSpeakerProfile {
  id: string
  label: string
  preset_id?: string
  voice: string
  style_prompt?: string
  sample_text: string
}

/** 配音音色预设 */
export interface VoicePresetProfile {
  id: string
  name: string
  voice: string
  style_prompt: string
  sample_text: string
}

/** 专业术语字库条目 */
export interface GlossaryTerm {
  id: string
  source: string
  replacement: string
  note: string
}

/** 最终导出设置 */
export interface AutomationExportSettings {
  resolution: 'original' | '720p' | '1080p' | 'custom'
  width: number
  height: number
  bitrate_enabled: boolean
  bitrate_kbps: number
}

/** 一键自动化偏好 */
export interface AutomationPreferences {
  output_format: 'mp4' | 'mkv' | 'mov' | 'webm'
  export_with_settings: boolean
  export_settings: AutomationExportSettings
  enable_effects: boolean
  subtitle_preset_id: number | null
  subtitle_language: string
  text_profile_id: number | null
  subtitle_recognition_mode: SubtitleRecognitionMode
  subtitle_operation: SubtitleTextOperation
  subtitle_target_language: string
  burn_subtitles: boolean
  enable_voice: boolean
  export_subtitle_only_when_voice: boolean
  voice_profile_id: number | null
  voice_mode: 'full' | 'batched' | 'segmented' | 'grouped'
  audio_mode: 'replace' | 'mix' | 'background'
  original_volume: number
  voice_batch_size: number
  voice_batch_chars: number
  voice_concurrency: number
  voice_group_size: number
  voice_group_chars: number
  voice_group_max_seconds: number
  voice_group_gap_ms: number
  multi_speaker_enabled: boolean
  voice_presets: VoicePresetProfile[]
  voice_speakers: VoiceSpeakerProfile[]
  glossary_terms: GlossaryTerm[]
  banned_words: string[]
  banned_word_action: 'warn' | 'block'
}

/** 配音音色 */
export interface VoiceOption {
  id: string
  name: string
  language: string
  style: string
  gender?: 'male' | 'female' | 'neutral'
}

/** 配音生成设置 */
export interface VoiceGenerateSettings {
  speed: number
  volume: number
  pitch: number
  format: 'mp3' | 'wav' | 'flac' | 'pcm' | 'opus'
  sample_rate: number
  bitrate: number
  channel: number
  emotion: string
  style_prompt: string
  language_boost: string
  intensity: number
  timbre: number
  voice_pitch: number
  sound_effects: string
  retry_count: number
  retry_interval_ms: number
}

/** 固定值或随机范围 */
export interface RandomRange {
  enabled: boolean
  random: boolean
  value: number | null
  min: number
  max: number
}

/** 画面处理配置 */
export interface ProcessingConfig {
  version?: number
  adjustments: {
    enabled: boolean
    brightness: RandomRange
    contrast: RandomRange
    saturation: RandomRange
    sharpness: RandomRange
    denoise: RandomRange
  }
  canvas: {
    enabled: boolean
    resolution: '720p' | '1080p' | 'original' | 'custom'
    mode: 'keep' | 'stretch' | 'crop' | 'blur_background'
    width: number
    height: number
    background_enabled: boolean
    reflection_enabled: boolean
    grid_enabled: boolean
  }
  transform: {
    enabled: boolean
    rotate_mode: 'none' | 'left90' | 'right90'
    flip_horizontal: boolean
    flip_vertical: boolean
    random_rotate: RandomRange
    remove_black_bars: boolean
    show_full_frame: boolean
  }
  timing: {
    enabled: boolean
    fps: RandomRange
    drop_frame: {
      enabled: boolean
      interval: RandomRange
    }
    dynamic_zoom: RandomRange
  }
  bitrate: {
    enabled: boolean
    mode: 'fixed' | 'multiplier'
    fixed_kbps: RandomRange
    multiplier: RandomRange
    quality_mode: 'balanced' | 'quality' | 'size'
  }
  acceleration?: {
    enabled: boolean
    mode: 'auto' | 'cpu' | 'nvidia' | 'intel' | 'amd'
    quality: 'balanced' | 'quality' | 'size'
  }
}

/** 画面处理预设 */
export interface ProcessingPreset {
  id: number
  name: string
  intensity: 'light' | 'standard' | 'strong' | 'custom'
  is_default: boolean
  config: ProcessingConfig
}

/** 日志条目 */
export interface LogEntry {
  timestamp: string
  level: 'info' | 'warn' | 'error'
  message: string
  source?: string
}

/** 后端活动日志条目 */
export interface BackendLogEntry {
  id: number
  timestamp: string
  level: 'info' | 'warn' | 'error'
  source: string
  message: string
}
