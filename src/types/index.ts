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
  formats: VideoFormat[]
  subtitles: SubtitleTrack[]
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

/** 下载任务 */
export interface DownloadTask {
  id: number
  video_id: number
  task_type: 'download' | 'effects' | 'subtitle' | 'voice' | 'export'
  status: 'pending' | 'downloading' | 'processing' | 'completed' | 'failed'
  progress: number
  output_path: string | null
  error_message: string | null
  created_at: string
  completed_at: string | null
}

/** 字幕预设 */
export interface SubtitlePreset {
  id: number
  name: string
  is_default: boolean
  line_mode: 'single' | 'double'
  font_name: string
  font_size: number
  font_color: string
  outline_color: string
  outline_width: number
  position: 'bottom' | 'top' | 'center'
  margin_v: number
}

/** API 配置 */
export interface ApiProfile {
  id: number
  name: string
  provider_type: string
  base_url: string
  model: string | null
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
}
