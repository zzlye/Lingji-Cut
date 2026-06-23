// src/lib/automationPayload.ts
// 自动化请求参数 - 把画面处理配置和一键偏好合并成后端请求

import { loadAutomationPreferences } from '@/lib/automationPreferences'
import { getActiveTextSystemPrompt } from '@/lib/textPromptPresets'
import { loadAutomationConfig } from '@/features/effects/EffectsPanel'
import type { AutomationRerunOverrides, AutomationStartParams } from '@/lib/api'

const LOCAL_VIDEO_PREFIX = 'local:'

/** 把系统文件路径包装成后端可识别的本地视频来源 */
export function toLocalVideoSource(filePath: string): string {
  return `${LOCAL_VIDEO_PREFIX}${filePath}`
}

/** 判断一键流程来源是否为本地视频 */
export function isLocalVideoSource(source: string): boolean {
  return source.trim().toLowerCase().startsWith(LOCAL_VIDEO_PREFIX)
}

/** 根据本地设置生成一键自动化请求 */
export function buildAutomationPayload(url: string): AutomationStartParams {
  const preferences = loadAutomationPreferences()
  const textProfileId = preferences.text_profile_id || undefined
  const voiceProfileId = preferences.enable_voice ? preferences.voice_profile_id || undefined : undefined
  const subtitleTargetLanguage = preferences.subtitle_target_language || (preferences.subtitle_operation === 'translate' ? 'zh-CN' : undefined)
  const speakerVoiceMap = preferences.enable_voice && preferences.multi_speaker_enabled
    ? Object.fromEntries(
      preferences.voice_speakers
        .filter((speaker) => speaker.label.trim() && speaker.voice.trim())
        .map((speaker) => [speaker.label.trim(), speaker.voice.trim()]),
    )
    : undefined
  const speakerVoiceStyles = preferences.enable_voice && preferences.multi_speaker_enabled
    ? Object.fromEntries(
      preferences.voice_speakers
        .filter((speaker) => speaker.label.trim() && (speaker.style_prompt || '').trim())
        .map((speaker) => [speaker.label.trim(), (speaker.style_prompt || '').trim()]),
    )
    : undefined

  return {
    url,
    enable_effects: preferences.enable_effects,
    processing_preset: loadAutomationConfig(),
    output_format: preferences.output_format,
    export_with_settings: preferences.export_with_settings,
    export_settings: preferences.export_settings,
    subtitle_preset_id: preferences.subtitle_preset_id || undefined,
    subtitle_language: preferences.subtitle_language && preferences.subtitle_language !== 'auto'
      ? preferences.subtitle_language
      : undefined,
    text_profile_id: textProfileId,
    subtitle_recognition_mode: preferences.subtitle_recognition_mode,
    gemini_audio_segment_seconds: preferences.gemini_audio_segment_seconds,
    gemini_audio_overlap_seconds: preferences.gemini_audio_overlap_seconds,
    gemini_audio_full_coverage: preferences.gemini_audio_full_coverage,
    gemini_audio_concurrency: preferences.gemini_audio_concurrency,
    gemini_audio_timeout_seconds: preferences.gemini_audio_timeout_seconds,
    subtitle_operation: preferences.subtitle_operation,
    subtitle_target_language: subtitleTargetLanguage,
    text_system_prompt: getActiveTextSystemPrompt(),
    burn_subtitles: preferences.burn_subtitles,
    enable_voice: preferences.enable_voice,
    export_subtitle_only_when_voice: preferences.enable_voice ? preferences.export_subtitle_only_when_voice : false,
    voice_profile_id: voiceProfileId,
    voice_mode: preferences.enable_voice ? 'batched' : undefined,
    audio_mode: preferences.enable_voice ? preferences.audio_mode : undefined,
    original_volume: preferences.enable_voice ? preferences.original_volume : undefined,
    voice_concurrency: preferences.enable_voice ? preferences.voice_concurrency : undefined,
    voice_min_gap_ms: preferences.enable_voice ? preferences.voice_min_gap_ms : undefined,
    multi_speaker_enabled: preferences.enable_voice ? preferences.multi_speaker_enabled : undefined,
    speaker_voice_map: speakerVoiceMap,
    speaker_voice_styles: speakerVoiceStyles,
    glossary_terms: preferences.glossary_terms,
    banned_words: preferences.banned_words,
    banned_word_action: preferences.banned_word_action,
  }
}

/** 继续/重试旧任务时，只刷新容易过期或用户刚调整过的 API 配置 */
export function buildAutomationRerunOverrides(): AutomationRerunOverrides {
  const preferences = loadAutomationPreferences()
  const speakerVoiceMap = preferences.enable_voice && preferences.multi_speaker_enabled
    ? Object.fromEntries(
      preferences.voice_speakers
        .filter((speaker) => speaker.label.trim() && speaker.voice.trim())
        .map((speaker) => [speaker.label.trim(), speaker.voice.trim()]),
    )
    : undefined
  const speakerVoiceStyles = preferences.enable_voice && preferences.multi_speaker_enabled
    ? Object.fromEntries(
      preferences.voice_speakers
        .filter((speaker) => speaker.label.trim() && (speaker.style_prompt || '').trim())
        .map((speaker) => [speaker.label.trim(), (speaker.style_prompt || '').trim()]),
    )
    : undefined

  return {
    text_profile_id: preferences.text_profile_id || undefined,
    text_system_prompt: getActiveTextSystemPrompt(),
    voice_profile_id: preferences.voice_profile_id || undefined,
    audio_mode: preferences.audio_mode,
    original_volume: preferences.original_volume,
    voice_concurrency: preferences.voice_concurrency,
    voice_min_gap_ms: preferences.voice_min_gap_ms,
    multi_speaker_enabled: preferences.multi_speaker_enabled,
    speaker_voice_map: speakerVoiceMap,
    speaker_voice_styles: speakerVoiceStyles,
    glossary_terms: preferences.glossary_terms,
    banned_words: preferences.banned_words,
    banned_word_action: preferences.banned_word_action,
  }
}
