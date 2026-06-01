// src/lib/automationPayload.ts
// 自动化请求参数 - 把画面处理配置和一键偏好合并成后端请求

import { loadAutomationPreferences } from '@/lib/automationPreferences'
import { loadAutomationConfig } from '@/features/effects/EffectsPanel'
import type { AutomationStartParams } from '@/lib/api'

/** 根据本地设置生成一键自动化请求 */
export function buildAutomationPayload(url: string): AutomationStartParams {
  const preferences = loadAutomationPreferences()
  const textProfileId = preferences.text_profile_id || undefined
  const voiceProfileId = preferences.voice_profile_id || undefined

  return {
    url,
    processing_preset: loadAutomationConfig(),
    output_format: preferences.output_format,
    subtitle_preset_id: preferences.subtitle_preset_id || undefined,
    subtitle_language: preferences.subtitle_language && preferences.subtitle_language !== 'auto'
      ? preferences.subtitle_language
      : undefined,
    text_profile_id: textProfileId,
    subtitle_operation: preferences.subtitle_operation,
    subtitle_target_language: preferences.subtitle_target_language || undefined,
    burn_subtitles: preferences.burn_subtitles,
    enable_voice: preferences.enable_voice,
    voice_profile_id: voiceProfileId,
    voice_mode: preferences.voice_mode,
    audio_mode: preferences.audio_mode,
    original_volume: preferences.original_volume,
  }
}
