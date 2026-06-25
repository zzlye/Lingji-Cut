// src/lib/subtitleRecognitionOptions.ts
// 字幕识别选项 - 设置页和工作台共用，保证一键流程参数一致

/** 本地 ASR 模型选项，auto 表示后端按设备和显存自动选择 */
export const SUBTITLE_LOCAL_MODEL_OPTIONS: Array<[string, string]> = [
  ['auto', '自动选择'],
  ['large-v3', 'large-v3（最准·较慢）'],
  ['large-v3-turbo', 'large-v3-turbo（准·更快）'],
  ['sensevoice', 'SenseVoice（轻声/事件更稳）'],
  ['qwen3-asr', 'Qwen3-ASR（高准确·较重）'],
  ['medium', 'medium（均衡）'],
  ['small', 'small（省显存）'],
  ['base', 'base（最快）'],
  ['tiny', 'tiny（最低占用）'],
]

/** 本地 ASR 模型值，保存偏好时用于过滤旧值或拼错值 */
export const SUBTITLE_LOCAL_MODEL_VALUES = SUBTITLE_LOCAL_MODEL_OPTIONS.map(([value]) => value)

/** 识别语言选项，auto 表示交给模型自动检测 */
export const SUBTITLE_RECOGNITION_LANGUAGE_OPTIONS: Array<[string, string]> = [
  ['auto', '自动检测'],
  ['en', '英文'],
  ['zh', '中文'],
  ['ja', '日文'],
  ['ko', '韩文'],
  ['es', '西班牙语'],
  ['fr', '法语'],
  ['de', '德语'],
  ['ru', '俄语'],
  ['pt', '葡萄牙语'],
  ['vi', '越南语'],
  ['th', '泰语'],
  ['id', '印尼语'],
  ['ar', '阿拉伯语'],
]
