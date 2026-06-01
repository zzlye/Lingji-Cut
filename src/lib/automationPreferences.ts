// src/lib/automationPreferences.ts
// 一键自动化偏好 - 统一保存字幕、文本 API、配音和导出参数

import type { AutomationPreferences } from '@/types'

/** 一键自动化偏好本地缓存键 */
const AUTOMATION_PREFERENCES_STORAGE_KEY = 'youtube-video-processor:auto-preferences'

/** 配音可选迁移标记，避免旧版本默认开启配音影响新流程 */
const VOICE_OPTIONAL_CONFIRMED_KEY = 'voice_optional_confirmed'

/** 默认一键自动化偏好 */
export const DEFAULT_AUTOMATION_PREFERENCES: AutomationPreferences = {
  output_format: 'mp4',
  subtitle_preset_id: null,
  subtitle_language: 'auto',
  text_profile_id: null,
  subtitle_operation: 'polish',
  subtitle_target_language: '',
  burn_subtitles: true,
  enable_voice: false,
  voice_profile_id: null,
  voice_mode: 'segmented',
  audio_mode: 'mix',
  original_volume: 0.25,
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

/** 读取一键自动化偏好 */
export function loadAutomationPreferences(): AutomationPreferences {
  if (typeof localStorage === 'undefined') {
    return DEFAULT_AUTOMATION_PREFERENCES
  }

  try {
    const saved = localStorage.getItem(AUTOMATION_PREFERENCES_STORAGE_KEY)
    const parsed = saved ? JSON.parse(saved) : {}
    const voiceWasExplicitlyChosen = parsed[VOICE_OPTIONAL_CONFIRMED_KEY] === true
    return {
      ...DEFAULT_AUTOMATION_PREFERENCES,
      ...parsed,
      subtitle_preset_id: normalizeId(parsed.subtitle_preset_id),
      text_profile_id: normalizeId(parsed.text_profile_id),
      enable_voice: Boolean(parsed.enable_voice && voiceWasExplicitlyChosen),
      voice_profile_id: normalizeId(parsed.voice_profile_id),
      original_volume: Math.min(1, Math.max(0, Number(parsed.original_volume ?? DEFAULT_AUTOMATION_PREFERENCES.original_volume))),
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
