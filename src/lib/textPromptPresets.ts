// src/lib/textPromptPresets.ts
// 文本 API 提示词预设 - 与 API 渠道、Key、模型配置分开保存

import type { ApiProfile, TextPromptPreset } from '@/types'

/** 默认系统提示词 */
export const DEFAULT_TEXT_SYSTEM_PROMPT = '你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。'

const PROMPT_PRESETS_STORAGE_KEY = 'lingjian-workshop:text-prompt-presets'
const ACTIVE_PROMPT_STORAGE_KEY = 'lingjian-workshop:active-text-prompt-preset'

/** 内置提示词预设，用户可继续修改或新增 */
const DEFAULT_PROMPT_PRESETS: TextPromptPreset[] = [
  {
    id: 'default-short-video',
    name: '短视频字幕通用',
    prompt: DEFAULT_TEXT_SYSTEM_PROMPT,
    description: '适合大多数翻译、润色和字幕对照场景。',
  },
  {
    id: 'strict-translation',
    name: '严格翻译不扩写',
    prompt: '你是专业字幕翻译助手。请逐条翻译字幕，严格保留原意、数字、专有名词和语气，不要补充剧情，不要解释，不要漏译。',
    description: '适合担心漏字、扩写或乱改意思的视频。',
  },
  {
    id: 'spoken-polish',
    name: '自然口播润色',
    prompt: '你是短视频口播字幕润色助手。请让字幕更自然、简洁、适合口播，但必须保留原意和信息量，不要编造新内容。',
    description: '适合解说类、教程类视频润色。',
  },
  {
    id: 'game-terms',
    name: '游戏术语友好',
    prompt: '你是游戏视频字幕处理助手。请保留游戏名、角色名、技能名、装备名和数值，不要把常见游戏术语翻错；中文表达要自然，不能扩写剧情。',
    description: '适合游戏解说、实况和攻略。',
  },
]

/** 生成本地稳定 ID */
function createPresetId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `prompt-${crypto.randomUUID()}`
  }
  return `prompt-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/** 清理提示词预设，避免损坏缓存影响页面加载 */
function normalizePreset(value: unknown, index: number): TextPromptPreset | null {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const prompt = String(raw.prompt || '').trim()
  if (!prompt) return null
  return {
    id: String(raw.id || `prompt-${index + 1}`),
    name: String(raw.name || `提示词预设 ${index + 1}`).trim(),
    prompt,
    description: String(raw.description || '').trim(),
  }
}

/** 读取提示词预设列表 */
export function loadTextPromptPresets(): TextPromptPreset[] {
  if (typeof localStorage === 'undefined') return DEFAULT_PROMPT_PRESETS
  try {
    const saved = localStorage.getItem(PROMPT_PRESETS_STORAGE_KEY)
    const parsed = saved ? JSON.parse(saved) : null
    const presets = Array.isArray(parsed)
      ? parsed.map(normalizePreset).filter(Boolean) as TextPromptPreset[]
      : []
    if (presets.length > 0) return presets
  } catch {
    // 缓存损坏时回退到内置预设。
  }
  saveTextPromptPresets(DEFAULT_PROMPT_PRESETS)
  return DEFAULT_PROMPT_PRESETS
}

/** 保存提示词预设列表 */
export function saveTextPromptPresets(presets: TextPromptPreset[]): TextPromptPreset[] {
  const normalized = presets
    .map(normalizePreset)
    .filter(Boolean) as TextPromptPreset[]
  const next = normalized.length > 0 ? normalized : DEFAULT_PROMPT_PRESETS
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(PROMPT_PRESETS_STORAGE_KEY, JSON.stringify(next))
    const activeId = loadActiveTextPromptPresetId()
    if (!next.some((preset) => preset.id === activeId)) {
      setActiveTextPromptPresetId(next[0].id)
    }
  }
  return next
}

/** 读取当前启用的提示词预设 ID */
export function loadActiveTextPromptPresetId(): string {
  if (typeof localStorage === 'undefined') return DEFAULT_PROMPT_PRESETS[0].id
  const activeId = localStorage.getItem(ACTIVE_PROMPT_STORAGE_KEY)
  return activeId || DEFAULT_PROMPT_PRESETS[0].id
}

/** 设置当前启用的提示词预设 ID */
export function setActiveTextPromptPresetId(presetId: string): string {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(ACTIVE_PROMPT_STORAGE_KEY, presetId)
  }
  return presetId
}

/** 读取当前启用的提示词预设 */
export function loadActiveTextPromptPreset(): TextPromptPreset | null {
  const presets = loadTextPromptPresets()
  const activeId = loadActiveTextPromptPresetId()
  return presets.find((preset) => preset.id === activeId) || presets[0] || null
}

/** 读取当前启用的系统提示词 */
export function getActiveTextSystemPrompt(): string {
  return loadActiveTextPromptPreset()?.prompt.trim() || DEFAULT_TEXT_SYSTEM_PROMPT
}

/** 新建提示词预设 */
export function createTextPromptPreset(name: string, prompt: string, description = ''): TextPromptPreset {
  return {
    id: createPresetId(),
    name: name.trim() || '未命名提示词',
    prompt: prompt.trim() || DEFAULT_TEXT_SYSTEM_PROMPT,
    description: description.trim(),
  }
}

/** 更新或插入提示词预设 */
export function upsertTextPromptPreset(preset: TextPromptPreset): TextPromptPreset[] {
  const presets = loadTextPromptPresets()
  const normalized = normalizePreset(preset, presets.length)
  if (!normalized) return presets
  const next = presets.some((item) => item.id === normalized.id)
    ? presets.map((item) => item.id === normalized.id ? normalized : item)
    : [...presets, normalized]
  return saveTextPromptPresets(next)
}

/** 删除提示词预设 */
export function deleteTextPromptPreset(presetId: string): TextPromptPreset[] {
  const next = loadTextPromptPresets().filter((preset) => preset.id !== presetId)
  return saveTextPromptPresets(next)
}

/** 把旧 API 配置里混着保存的 system_prompt 迁移成独立提示词预设 */
export function migrateTextPromptPresetsFromProfiles(profiles: ApiProfile[]): TextPromptPreset[] {
  const hadActivePreset = typeof localStorage !== 'undefined' && Boolean(localStorage.getItem(ACTIVE_PROMPT_STORAGE_KEY))
  const presets = loadTextPromptPresets()
  const existingPrompts = new Set(presets.map((preset) => preset.prompt.trim()))
  const migrated: TextPromptPreset[] = []
  for (const profile of profiles) {
    if (!profile.extra_params) continue
    try {
      const settings = JSON.parse(profile.extra_params) as { system_prompt?: unknown }
      const prompt = String(settings.system_prompt || '').trim()
      if (!prompt || existingPrompts.has(prompt)) continue
      existingPrompts.add(prompt)
      migrated.push(createTextPromptPreset(`从旧配置迁移 · ${profile.name}`, prompt, '由旧版文本 API 配置里的系统提示词自动迁移。'))
    } catch {
      // 单个配置损坏不影响其它配置迁移。
    }
  }
  if (!migrated.length) return presets
  const next = saveTextPromptPresets([...presets, ...migrated])
  if (!hadActivePreset) setActiveTextPromptPresetId(migrated[0].id)
  return next
}
