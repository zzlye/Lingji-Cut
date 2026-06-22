// src/lib/automationPreferences.ts
// 一键自动化偏好 - 统一保存字幕、文本 API、配音和导出参数

import type { AutomationPreferences } from '@/types'

/** 一键自动化偏好本地缓存键 */
const AUTOMATION_PREFERENCES_STORAGE_KEY = 'lingjian-workshop:auto-preferences'

/** 旧版本缓存键，用于改名后迁移用户已保存的一键设置 */
const LEGACY_AUTOMATION_PREFERENCES_STORAGE_KEY = 'youtube-video-processor:auto-preferences'

/** 配音可选迁移标记，避免旧版本默认开启配音影响新流程 */
const VOICE_OPTIONAL_CONFIRMED_KEY = 'voice_optional_confirmed'

/** 默认一键自动化偏好 */
export const DEFAULT_AUTOMATION_PREFERENCES: AutomationPreferences = {
  output_format: 'mp4',
  export_with_settings: true,
  export_settings: {
    resolution: 'original',
    width: 1920,
    height: 1080,
    bitrate_enabled: false,
    bitrate_kbps: 2200,
  },
  enable_effects: true,
  subtitle_preset_id: null,
  subtitle_language: 'auto',
  text_profile_id: null,
  subtitle_recognition_mode: 'local',
  subtitle_operation: 'polish',
  subtitle_target_language: 'zh-CN',
  burn_subtitles: true,
  enable_voice: false,
  export_subtitle_only_when_voice: false,
  voice_profile_id: null,
  voice_mode: 'batched',
  audio_mode: 'mix',
  original_volume: 0.25,
  voice_batch_size: 16,
  voice_batch_chars: 1800,
  voice_concurrency: 2,
  multi_speaker_enabled: false,
  voice_speakers: [
    { id: 'narrator', label: '旁白', voice: 'alloy', sample_text: '旁白负责解释画面和推进节奏。' },
    { id: 'speaker_a', label: '角色 A', voice: 'nova', sample_text: '这是角色 A 的一句对话试听。' },
    { id: 'speaker_b', label: '角色 B', voice: 'onyx', sample_text: '这是角色 B 的一句对话试听。' },
  ],
  glossary_terms: [],
  banned_words: [],
  banned_word_action: 'warn',
}

/** 规范数字 ID，避免把 NaN 写入一键参数 */
function normalizeId(value: unknown): number | null {
  const id = Number(value)
  return Number.isFinite(id) && id > 0 ? id : null
}

/** 规范说话人音色映射，避免空标签或空音色进入自动化请求 */
function normalizeVoiceSpeakers(value: unknown): AutomationPreferences['voice_speakers'] {
  const raw = Array.isArray(value) ? value : DEFAULT_AUTOMATION_PREFERENCES.voice_speakers
  const speakers = raw
    .map((item, index) => {
      const data = item && typeof item === 'object' ? item as Record<string, unknown> : {}
      const label = String(data.label || '').trim()
      const voice = String(data.voice || '').trim()
      if (!label || !voice) return null
      return {
        id: String(data.id || `speaker_${index + 1}`),
        label,
        voice,
        sample_text: String(data.sample_text || '').trim() || `${label} 的配音试听。`,
      }
    })
    .filter(Boolean) as AutomationPreferences['voice_speakers']
  return speakers.length > 0 ? speakers : DEFAULT_AUTOMATION_PREFERENCES.voice_speakers
}

/** 规范术语字库，空来源词不保存 */
function normalizeGlossaryTerms(value: unknown): AutomationPreferences['glossary_terms'] {
  if (!Array.isArray(value)) return []
  return value
    .map((item, index) => {
      const data = item && typeof item === 'object' ? item as Record<string, unknown> : {}
      const source = String(data.source || '').trim()
      if (!source) return null
      return {
        id: String(data.id || `term_${index + 1}`),
        source,
        replacement: String(data.replacement || '').trim(),
        note: String(data.note || '').trim(),
      }
    })
    .filter(Boolean) as AutomationPreferences['glossary_terms']
}

/** 规范禁词列表，支持旧数据里的换行字符串或数组 */
function normalizeBannedWords(value: unknown): string[] {
  const raw = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(/\r?\n|,/)
      : []
  return Array.from(new Set(raw.map((item) => String(item).trim()).filter(Boolean)))
}

/** 规范一键配音生成模式，旧版 segmented 继续保留 */
function normalizeVoiceMode(value: unknown): AutomationPreferences['voice_mode'] {
  const mode = String(value || '')
  return ['full', 'batched', 'segmented'].includes(mode)
    ? mode as AutomationPreferences['voice_mode']
    : DEFAULT_AUTOMATION_PREFERENCES.voice_mode
}

/** 规范配音批处理数值，避免缓存里写入过大值拖垮接口 */
function normalizeVoiceNumber(value: unknown, defaultValue: number, min: number, max: number): number {
  const numberValue = Math.round(Number(value ?? defaultValue))
  return Math.min(max, Math.max(min, Number.isFinite(numberValue) ? numberValue : defaultValue))
}

/** 规范最终导出设置，保证旧缓存升级后仍有完整默认值 */
function normalizeExportSettings(value: unknown): AutomationPreferences['export_settings'] {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const resolution = ['original', '720p', '1080p', 'custom'].includes(String(raw.resolution || ''))
    ? String(raw.resolution) as AutomationPreferences['export_settings']['resolution']
    : DEFAULT_AUTOMATION_PREFERENCES.export_settings.resolution
  return {
    resolution,
    width: Math.max(320, Number(raw.width ?? DEFAULT_AUTOMATION_PREFERENCES.export_settings.width) || DEFAULT_AUTOMATION_PREFERENCES.export_settings.width),
    height: Math.max(180, Number(raw.height ?? DEFAULT_AUTOMATION_PREFERENCES.export_settings.height) || DEFAULT_AUTOMATION_PREFERENCES.export_settings.height),
    bitrate_enabled: Boolean(raw.bitrate_enabled),
    bitrate_kbps: Math.min(20000, Math.max(200, Number(raw.bitrate_kbps ?? DEFAULT_AUTOMATION_PREFERENCES.export_settings.bitrate_kbps) || DEFAULT_AUTOMATION_PREFERENCES.export_settings.bitrate_kbps)),
  }
}

/** 规范字幕处理方式，区分完全跳过和使用原字幕 */
function normalizeSubtitleOperation(value: unknown): AutomationPreferences['subtitle_operation'] {
  const operation = String(value || '')
  return ['skip', 'none', 'generate', 'translate', 'polish'].includes(operation)
    ? operation as AutomationPreferences['subtitle_operation']
    : DEFAULT_AUTOMATION_PREFERENCES.subtitle_operation
}

/** 读取一键自动化偏好 */
export function loadAutomationPreferences(): AutomationPreferences {
  if (typeof localStorage === 'undefined') {
    return DEFAULT_AUTOMATION_PREFERENCES
  }

  try {
    const saved = localStorage.getItem(AUTOMATION_PREFERENCES_STORAGE_KEY) || localStorage.getItem(LEGACY_AUTOMATION_PREFERENCES_STORAGE_KEY)
    if (saved && !localStorage.getItem(AUTOMATION_PREFERENCES_STORAGE_KEY)) {
      localStorage.setItem(AUTOMATION_PREFERENCES_STORAGE_KEY, saved)
    }
    const parsed = saved ? JSON.parse(saved) : {}
    const voiceWasExplicitlyChosen = parsed[VOICE_OPTIONAL_CONFIRMED_KEY] === true
    return {
      ...DEFAULT_AUTOMATION_PREFERENCES,
      ...parsed,
      export_with_settings: parsed.export_with_settings !== false,
      export_settings: normalizeExportSettings(parsed.export_settings),
      enable_effects: parsed.enable_effects !== false,
      subtitle_preset_id: normalizeId(parsed.subtitle_preset_id),
      text_profile_id: normalizeId(parsed.text_profile_id),
      subtitle_recognition_mode: ['local', 'gemini_full', 'gemini_align'].includes(parsed.subtitle_recognition_mode)
        ? parsed.subtitle_recognition_mode
        : 'local',
      subtitle_operation: normalizeSubtitleOperation(parsed.subtitle_operation),
      enable_voice: Boolean(parsed.enable_voice && voiceWasExplicitlyChosen),
      export_subtitle_only_when_voice: Boolean(parsed.export_subtitle_only_when_voice),
      voice_profile_id: normalizeId(parsed.voice_profile_id),
      voice_mode: normalizeVoiceMode(parsed.voice_mode),
      original_volume: Math.min(1, Math.max(0, Number(parsed.original_volume ?? DEFAULT_AUTOMATION_PREFERENCES.original_volume))),
      voice_batch_size: normalizeVoiceNumber(parsed.voice_batch_size, DEFAULT_AUTOMATION_PREFERENCES.voice_batch_size, 1, 80),
      voice_batch_chars: normalizeVoiceNumber(parsed.voice_batch_chars, DEFAULT_AUTOMATION_PREFERENCES.voice_batch_chars, 100, 12000),
      voice_concurrency: normalizeVoiceNumber(parsed.voice_concurrency, DEFAULT_AUTOMATION_PREFERENCES.voice_concurrency, 1, 8),
      multi_speaker_enabled: Boolean(parsed.multi_speaker_enabled),
      voice_speakers: normalizeVoiceSpeakers(parsed.voice_speakers),
      glossary_terms: normalizeGlossaryTerms(parsed.glossary_terms),
      banned_words: normalizeBannedWords(parsed.banned_words),
      banned_word_action: parsed.banned_word_action === 'block' ? 'block' : 'warn',
    }
  } catch {
    return DEFAULT_AUTOMATION_PREFERENCES
  }
}

/** 保存一键自动化偏好 */
export function saveAutomationPreferences(updates: Partial<AutomationPreferences>): AutomationPreferences {
  const next = {
    ...loadAutomationPreferences(),
    ...updates,
  }

  if (typeof localStorage !== 'undefined') {
    const persisted = {
      ...next,
      ...(Object.prototype.hasOwnProperty.call(updates, 'enable_voice') ? { [VOICE_OPTIONAL_CONFIRMED_KEY]: true } : {}),
    }
    localStorage.setItem(AUTOMATION_PREFERENCES_STORAGE_KEY, JSON.stringify(persisted))
  }

  return next
}
