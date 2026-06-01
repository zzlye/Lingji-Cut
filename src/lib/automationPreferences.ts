// src/lib/automationPreferences.ts
// 一键自动化偏好 - 统一保存字幕、文本 API、配音和导出参数

import type { AutomationPreferences } from '@/types'

/** 一键自动化偏好本地缓存键 */
const AUTOMATION_PREFERENCES_STORAGE_KEY = 'youtube-video-processor:auto-preferences'

/** 默认一键自动化偏好 */
export const DEFAULT_AUTOMATION_PREFERENCES: AutomationPreferences = {
  output_format: 'mp4',
  subtitle_preset_id: null,
  subtitle_language: 'auto',
  text_profile_id: null,
  subtitle_operation: 'polish',
  subtitle_target_language: '',
  burn_subtitles: true,
  enable_voice: true,
  voice_profile_id: null,
  voice_mode: 'segmented',
  audio_mode: 'mix',
  original_volume: 0.25,
}

/** 规范数字 ID，避免把 NaN 写入一键参数 */
function normalizeId(value: unknown): number | null {
  const id = Number(value)
  return Number.isFinite(id) && id > 0 ? id : null
}

/** 读取一键自动化偏好 */
export function loadAutomationPreferences(): AutomationPreferences {
  if (typeof localStorage === 'undefined') {
    return DEFAULT_AUTOMATION_PREFERENCES
  }

  try {
    const saved = localStorage.getItem(AUTOMATION_PREFERENCES_STORAGE_KEY)
    const parsed = saved ? JSON.parse(saved) : {}
    return {
      ...DEFAULT_AUTOMATION_PREFERENCES,
      ...parsed,
      subtitle_preset_id: normalizeId(parsed.subtitle_preset_id),
      text_profile_id: normalizeId(parsed.text_profile_id),
      voice_profile_id: normalizeId(parsed.voice_profile_id),
      original_volume: Math.min(1, Math.max(0, Number(parsed.original_volume ?? DEFAULT_AUTOMATION_PREFERENCES.original_volume))),
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
    localStorage.setItem(AUTOMATION_PREFERENCES_STORAGE_KEY, JSON.stringify(next))
  }

  return next
}
