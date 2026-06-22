// src/lib/automationPreferences.ts
// 一键自动化偏好 - 统一保存字幕、文本 API、配音和导出参数

import type { AutomationPreferences } from '@/types'

/** 一键自动化偏好本地缓存键 */
const AUTOMATION_PREFERENCES_STORAGE_KEY = 'lingjian-workshop:auto-preferences'

/** 旧版本缓存键，用于改名后迁移用户已保存的一键设置 */
const LEGACY_AUTOMATION_PREFERENCES_STORAGE_KEY = 'youtube-video-processor:auto-preferences'

/** 配音可选迁移标记，避免旧版本默认开启配音影响新流程 */
const VOICE_OPTIONAL_CONFIRMED_KEY = 'voice_optional_confirmed'

/** 替换原声确认标记，避免旧缓存误把 BGM 和游戏声音静音 */
const AUDIO_REPLACE_CONFIRMED_KEY = 'audio_replace_confirmed'

/** 自然分组模式迁移标记，避免旧版逐条并发继续造成抢话听感 */
const VOICE_NATURAL_MODE_MIGRATED_KEY = 'voice_natural_mode_migrated'

/** 严格时间轴模式迁移标记，避免旧版自然分组继续造成逐行字幕错位 */
const VOICE_STRICT_TIMELINE_MODE_MIGRATED_KEY = 'voice_strict_timeline_mode_migrated'

/** 尾音避让默认值迁移标记，避免旧缓存继续把 160ms 发给后端 */
const VOICE_GAP_300_MIGRATED_KEY = 'voice_gap_300_migrated'

/** 旧版默认风格偏平淡，升级时只替换这些精确默认值 */
const LEGACY_DEFAULT_VOICE_STYLE_PROMPTS: Record<string, string> = {
  preset_narrator: '用中性自然解说口吻，语气稳定，节奏清晰。',
  preset_speaker_a: '用年轻自然的女声对话口吻，表达轻松。',
  preset_speaker_b: '用沉稳有辨识度的男声对话口吻，语气明确。',
  narrator: '用中性自然解说口吻，语气稳定，节奏清晰。',
  speaker_a: '用年轻自然的女声对话口吻，表达轻松。',
  speaker_b: '用沉稳有辨识度的男声对话口吻，语气明确。',
}

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
  voice_min_gap_ms: 300,
  voice_group_size: 6,
  voice_group_chars: 500,
  voice_group_max_seconds: 12,
  voice_group_gap_ms: 800,
  multi_speaker_enabled: false,
  voice_speakers: [
    { id: 'narrator', label: '旁白', voice: 'alloy', style_prompt: '用有画面感的游戏解说口吻，语气有起伏；遇到转折、危险、惊讶时加强重音，句尾自然收住，不要像朗读新闻。', sample_text: '旁白负责带出紧张感、解释画面，并自然推进节奏。' },
    { id: 'speaker_a', label: '角色 A', voice: 'nova', style_prompt: '用年轻自然的女声对话口吻，反应真实，情绪轻松但有变化；短句要像聊天，不要像念稿。', sample_text: '这是角色 A 的一句女声对话试听，带一点轻松反应。' },
    { id: 'speaker_b', label: '角色 B', voice: 'onyx', style_prompt: '用沉稳有辨识度的男声对话口吻，语气明确，关键字有重音；保持角色感，不要平铺直叙。', sample_text: '这是角色 B 的一句男声对话试听，语气要明确。' },
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

/** 查找新版默认说话人，供旧默认提示词迁移使用 */
function defaultVoiceSpeakerById(id: string): AutomationPreferences['voice_speakers'][number] | undefined {
  return DEFAULT_AUTOMATION_PREFERENCES.voice_speakers.find((speaker) => speaker.id === id)
}

/** 只把软件旧默认风格提示升级为更有情绪的新版提示，不覆盖用户自定义内容 */
function migrateLegacyVoiceStylePrompt(id: string, stylePrompt: string, fallback?: string): string {
  const trimmed = stylePrompt.trim()
  return trimmed && LEGACY_DEFAULT_VOICE_STYLE_PROMPTS[id] !== trimmed
    ? trimmed
    : fallback || trimmed
}

/** 判断缓存里是否还存在旧版默认风格提示，决定是否回写迁移结果 */
function hasLegacyDefaultVoiceStylePrompts(value: unknown): boolean {
  if (!Array.isArray(value)) return false
  return value.some((item) => {
    const data = item && typeof item === 'object' ? item as Record<string, unknown> : {}
    const id = String(data.id || '').trim()
    const stylePrompt = String(data.style_prompt || '').trim()
    return Boolean(id && stylePrompt && LEGACY_DEFAULT_VOICE_STYLE_PROMPTS[id] === stylePrompt)
  })
}

/** 规范说话人音色映射，避免空标签或空音色进入自动化请求；兼容旧 preset 数据迁移 */
function normalizeVoiceSpeakers(value: unknown, legacyPresets?: unknown[]): AutomationPreferences['voice_speakers'] {
  const raw = Array.isArray(value) ? value : DEFAULT_AUTOMATION_PREFERENCES.voice_speakers
  // 旧版 voice_presets 数据，用于将 preset_id 引用的音色信息内联到 speaker
  const presetList = Array.isArray(legacyPresets) ? legacyPresets : []
  const speakers = raw
    .map((item, index) => {
      const data = item && typeof item === 'object' ? item as Record<string, unknown> : {}
      const label = String(data.label || '').trim()
      const presetId = String(data.preset_id || '').trim()
      // 从旧预设中查找对应音色，用于兼容迁移
      const preset = presetList.find((p: any) => p && p.id === presetId) as Record<string, unknown> | undefined
      const voice = String(data.voice || preset?.voice || '').trim()
      if (!label || !voice) return null
      const id = String(data.id || `speaker_${index + 1}`)
      const defaultSpeaker = defaultVoiceSpeakerById(id)
      return {
        id,
        label,
        voice,
        style_prompt: migrateLegacyVoiceStylePrompt(id, String(data.style_prompt || preset?.style_prompt || ''), defaultSpeaker?.style_prompt || (preset?.style_prompt as string) || ''),
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
  return ['full', 'batched', 'segmented', 'grouped'].includes(mode)
    ? mode as AutomationPreferences['voice_mode']
    : DEFAULT_AUTOMATION_PREFERENCES.voice_mode
}

/** 规范音频合成模式，旧版未确认的 replace 一律迁回混合原声 */
function normalizeAudioMode(value: unknown, replaceConfirmed: boolean): AutomationPreferences['audio_mode'] {
  const mode = String(value || '')
  if (mode === 'replace') {
    return replaceConfirmed ? 'replace' : DEFAULT_AUTOMATION_PREFERENCES.audio_mode
  }
  if (mode === 'mix' || mode === 'background') {
    return mode
  }
  return DEFAULT_AUTOMATION_PREFERENCES.audio_mode
}

/** 规范配音批处理数值，避免缓存里写入过大值拖垮接口 */
function normalizeVoiceNumber(value: unknown, defaultValue: number, min: number, max: number): number {
  const numberValue = Math.round(Number(value ?? defaultValue))
  return Math.min(max, Math.max(min, Number.isFinite(numberValue) ? numberValue : defaultValue))
}

/** 规范尾音避让；旧版默认 160ms 会自动迁移到 300ms，用户设置的其它数值保留 */
function normalizeVoiceMinGapMs(value: unknown, migrated: boolean): number {
  const hasValue = value !== undefined && value !== null && String(value).trim() !== ''
  const numberValue = Math.round(Number(value))
  if (!migrated && (!hasValue || numberValue === 160)) {
    return DEFAULT_AUTOMATION_PREFERENCES.voice_min_gap_ms
  }
  return normalizeVoiceNumber(value, DEFAULT_AUTOMATION_PREFERENCES.voice_min_gap_ms, 0, 2000)
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
    const audioReplaceWasExplicitlyChosen = parsed[AUDIO_REPLACE_CONFIRMED_KEY] === true
    const voiceModeWasMigrated = parsed[VOICE_NATURAL_MODE_MIGRATED_KEY] === true
    const strictTimelineModeWasMigrated = parsed[VOICE_STRICT_TIMELINE_MODE_MIGRATED_KEY] === true
    const voiceGapWasMigrated = parsed[VOICE_GAP_300_MIGRATED_KEY] === true
    const normalizedVoiceMode = normalizeVoiceMode(parsed.voice_mode)
    const voiceMode = !strictTimelineModeWasMigrated && normalizedVoiceMode === 'grouped'
      ? 'batched'
      : normalizedVoiceMode
    const voiceSpeakers = normalizeVoiceSpeakers(parsed.voice_speakers, parsed.voice_presets)
    const shouldPersistVoiceMigration = hasLegacyDefaultVoiceStylePrompts(parsed.voice_presets) || hasLegacyDefaultVoiceStylePrompts(parsed.voice_speakers)
    const preferences = {
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
      voice_mode: voiceMode,
      audio_mode: normalizeAudioMode(parsed.audio_mode, audioReplaceWasExplicitlyChosen),
      original_volume: Math.min(1, Math.max(0, Number(parsed.original_volume ?? DEFAULT_AUTOMATION_PREFERENCES.original_volume))),
      voice_batch_size: normalizeVoiceNumber(parsed.voice_batch_size, DEFAULT_AUTOMATION_PREFERENCES.voice_batch_size, 1, 80),
      voice_batch_chars: normalizeVoiceNumber(parsed.voice_batch_chars, DEFAULT_AUTOMATION_PREFERENCES.voice_batch_chars, 100, 12000),
      voice_concurrency: normalizeVoiceNumber(parsed.voice_concurrency, DEFAULT_AUTOMATION_PREFERENCES.voice_concurrency, 1, 8),
      voice_min_gap_ms: normalizeVoiceMinGapMs(parsed.voice_min_gap_ms, voiceGapWasMigrated),
      voice_group_size: normalizeVoiceNumber(parsed.voice_group_size, DEFAULT_AUTOMATION_PREFERENCES.voice_group_size, 1, 12),
      voice_group_chars: normalizeVoiceNumber(parsed.voice_group_chars, DEFAULT_AUTOMATION_PREFERENCES.voice_group_chars, 80, 2000),
      voice_group_max_seconds: Math.min(30, Math.max(1, Number(parsed.voice_group_max_seconds ?? DEFAULT_AUTOMATION_PREFERENCES.voice_group_max_seconds) || DEFAULT_AUTOMATION_PREFERENCES.voice_group_max_seconds)),
      voice_group_gap_ms: normalizeVoiceNumber(parsed.voice_group_gap_ms, DEFAULT_AUTOMATION_PREFERENCES.voice_group_gap_ms, 0, 5000),
      multi_speaker_enabled: Boolean(parsed.multi_speaker_enabled),
      voice_speakers: voiceSpeakers,
      glossary_terms: normalizeGlossaryTerms(parsed.glossary_terms),
      banned_words: normalizeBannedWords(parsed.banned_words),
      banned_word_action: parsed.banned_word_action === 'block' ? 'block' : 'warn',
    }
    if (saved && (!voiceModeWasMigrated || !strictTimelineModeWasMigrated || !voiceGapWasMigrated || shouldPersistVoiceMigration) && typeof localStorage !== 'undefined') {
      localStorage.setItem(AUTOMATION_PREFERENCES_STORAGE_KEY, JSON.stringify({
        ...preferences,
        [VOICE_NATURAL_MODE_MIGRATED_KEY]: true,
        [VOICE_STRICT_TIMELINE_MODE_MIGRATED_KEY]: true,
        [VOICE_GAP_300_MIGRATED_KEY]: true,
      }))
    }
    return preferences
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
      [VOICE_NATURAL_MODE_MIGRATED_KEY]: true,
      [VOICE_STRICT_TIMELINE_MODE_MIGRATED_KEY]: true,
      [VOICE_GAP_300_MIGRATED_KEY]: true,
      ...(Object.prototype.hasOwnProperty.call(updates, 'enable_voice') ? { [VOICE_OPTIONAL_CONFIRMED_KEY]: true } : {}),
      ...(Object.prototype.hasOwnProperty.call(updates, 'audio_mode') ? { [AUDIO_REPLACE_CONFIRMED_KEY]: updates.audio_mode === 'replace' } : {}),
    }
    localStorage.setItem(AUTOMATION_PREFERENCES_STORAGE_KEY, JSON.stringify(persisted))
  }

  return next
}
