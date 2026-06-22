// src/features/voice/VoiceConfigPanel.tsx
// 配音配置面板 - 管理配音 API、音色、试听和生成参数
// 交互重做：渠道/音色/试听露出，生成参数与多说话人收进高级折叠

import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { Pause, Play, Plus, Trash2, Volume2 } from 'lucide-react'
import { BASE_URL, profileApi, voiceApi } from '@/lib/api'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { ApiProfile, TextModelOption, VoiceGenerateSettings, VoiceOption, VoicePresetProfile, VoiceSpeakerProfile } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { TextField, SecretField, SelectField, SwitchField, SliderField, TextareaField, NumberField, type FieldOption } from '@/components/fields'

/** 配音渠道配置 */
const VOICE_PROVIDERS = [
  { id: 'openai_tts', name: 'OpenAI TTS', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini-tts' },
  { id: 'gemini_tts', name: 'Gemini TTS', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.5-flash-preview-tts' },
  { id: 'minimax_tts', name: 'MiniMax T2A', baseUrl: 'https://api.minimax.io/v1', model: 'speech-2.8-hd' },
  { id: 'xiaomi_mimo_tts', name: '小米 MiMo TTS', baseUrl: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2.5-tts' },
  { id: 'custom_tts', name: '自定义 OpenAI 兼容', baseUrl: '', model: '' },
]

const VOICE_PROVIDER_DEFAULT_BASE_URLS = VOICE_PROVIDERS.map((provider) => provider.baseUrl).filter(Boolean)

/** 默认试听文本 */
const DEFAULT_PREVIEW_TEXT = '这是一段配音试听，用来确认音色、语速、音量和情绪是否适合当前视频。'

/** 配音参数默认值 */
function createDefaultSettings(): VoiceGenerateSettings {
  return {
    speed: 1, volume: 1, pitch: 0, format: 'mp3', sample_rate: 32000, bitrate: 128000, channel: 1,
    emotion: '', style_prompt: '', language_boost: 'auto', intensity: 0, timbre: 0, voice_pitch: 0, sound_effects: '',
    xiaomi_voice_design_prompt: '', xiaomi_voice_clone_audio_path: '', xiaomi_voice_clone_audio_name: '',
    retry_count: 2, retry_interval_ms: 1200,
  }
}

/** 配音 API 表单 */
function createProfileForm() {
  const provider = VOICE_PROVIDERS[0]
  return { name: 'OpenAI 配音', provider_type: provider.id, base_url: provider.baseUrl, api_key: '', model: provider.model, custom_model: '' }
}

/** 面板内操作反馈 */
type PanelNotice = { type: 'info' | 'success' | 'warning' | 'error'; message: string } | null

const FORMAT_OPTIONS: FieldOption[] = [['mp3', 'MP3'], ['wav', 'WAV'], ['flac', 'FLAC'], ['pcm', 'PCM'], ['opus', 'OPUS']]
const SAMPLE_RATE_OPTIONS: FieldOption[] = ['16000', '22050', '24000', '32000', '44100'].map((v) => [v, `${v} Hz`])
const BITRATE_OPTIONS: FieldOption[] = ['32000', '64000', '128000', '256000'].map((v) => [v, `${Number(v) / 1000} kbps`])
const CHANNEL_OPTIONS: FieldOption[] = [['1', '单声道'], ['2', '立体声']]
const EMOTION_OPTIONS: FieldOption[] = [['', '自动'], ['happy', '开心'], ['sad', '悲伤'], ['angry', '愤怒'], ['calm', '平静'], ['surprised', '惊讶'], ['whisper', '耳语']]
const LANG_BOOST_OPTIONS: FieldOption[] = [['auto', '自动'], ['Chinese', '中文'], ['English', '英文'], ['Japanese', '日文'], ['Korean', '韩文']]

/** 音色听感倾向展示文案 */
function voiceGenderLabel(gender?: VoiceOption['gender']): string {
  if (gender === 'male') return '偏男声'
  if (gender === 'female') return '偏女声'
  return '中性'
}

/** 下拉里展示音色名称、听感倾向和风格 */
function voiceOptionLabel(item: VoiceOption): string {
  return `${item.name} · ${voiceGenderLabel(item.gender)} · ${item.style}`
}

/** 小米 VoiceDesign 的 voice 值编码 */
function encodeXiaomiVoiceDesign(prompt: string): string {
  const normalized = prompt.trim()
  return normalized ? `voice_design:${normalized}` : 'voice_design'
}

/** 小米 VoiceDesign 的 voice 值解码 */
function decodeXiaomiVoiceDesign(value: string): string {
  const normalized = String(value || '').trim()
  return normalized.startsWith('voice_design:') ? normalized.slice('voice_design:'.length).trim() : ''
}

/** 从旧版纯文本或新版编码里读取可编辑的文字音色描述 */
function editableXiaomiVoiceDesignPrompt(value: string): string {
  const normalized = String(value || '').trim()
  return decodeXiaomiVoiceDesign(normalized)
}

/** 小米 VoiceClone 的 voice 值编码 */
function encodeXiaomiVoiceClonePath(path: string): string {
  const normalized = path.trim()
  return normalized ? `voice_clone_path:${normalized}` : 'voice_clone'
}

/** 小米 VoiceClone 的 voice 值解码 */
function decodeXiaomiVoiceClonePath(value: string): string {
  const normalized = String(value || '').trim()
  if (normalized.startsWith('voice_clone_path:')) return normalized.slice('voice_clone_path:'.length).trim()
  if (normalized.startsWith('voice_clone:')) return normalized.slice('voice_clone:'.length).trim()
  return ''
}

/** 从旧版本地路径或新版编码里读取可编辑的克隆样本路径 */
function editableXiaomiVoiceClonePath(value: string): string {
  const normalized = String(value || '').trim()
  return decodeXiaomiVoiceClonePath(normalized) || (/\.(mp3|wav)$/i.test(normalized) ? normalized : '')
}

/** 从本地路径里取文件名，Windows 和 POSIX 路径都兼容 */
function basenameFromPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path
}

/** 展示编码后的特殊音色值 */
function voiceValueLabel(value: string, voices: VoiceOption[] = []): string {
  const matched = voices.find((item) => item.id === value)
  if (matched) return voiceOptionLabel(matched)
  const designPrompt = decodeXiaomiVoiceDesign(value)
  if (designPrompt) return `文字定制 · ${designPrompt}`
  const clonePath = decodeXiaomiVoiceClonePath(value)
  if (clonePath) return `克隆样本 · ${basenameFromPath(clonePath)}`
  return value || '未选择'
}

/** 根据预设构造下拉选项 */
function voicePresetOptions(presets: VoicePresetProfile[]): FieldOption[] {
  return presets.map((preset) => [preset.id, `${preset.name} · ${voiceValueLabel(preset.voice)}`] as FieldOption)
}

/** 合并音色目录、预设和当前值，避免下拉里丢失自定义 voice id */
function voiceOptionsWithCustom(voices: VoiceOption[], presets: VoicePresetProfile[], currentVoice: string): FieldOption[] {
  const options = [
    ...voices.map((item) => [item.id, voiceOptionLabel(item)] as FieldOption),
    ...presets.map((preset) => [preset.voice, `${preset.name} · ${voiceValueLabel(preset.voice, voices)}`] as FieldOption),
    currentVoice ? [currentVoice, voiceValueLabel(currentVoice, voices)] as FieldOption : null,
  ].filter(Boolean) as FieldOption[]
  return options.filter((item, index, list) => list.findIndex((candidate) => candidate[0] === item[0]) === index)
}

/** 按角色文案给小米内置音色选一个默认值 */
function defaultXiaomiBuiltinVoice(label: string, style: string, index: number): string {
  const text = `${label} ${style}`
  if (/旁白|解说|narrator/i.test(text)) return 'mimo_default'
  if (/女|female|lady|girl|角色 A/i.test(text)) return index % 2 === 0 ? '茉莉' : '冰糖'
  if (/男|male|gentleman|boy|角色 B/i.test(text)) return index % 2 === 0 ? '白桦' : '苏打'
  return ['mimo_default', '茉莉', '白桦', '冰糖', '苏打'][index % 5]
}

/** 按角色文案生成一个可编辑的小米 VoiceDesign 默认描述 */
function defaultXiaomiDesignPrompt(label: string, style: string, index: number): string {
  const text = `${label} ${style}`
  if (/旁白|解说|narrator/i.test(text)) return '中性自然旁白声，普通话标准，语气稳定，节奏清晰，适合视频解说。'
  if (/女|female|lady|girl|角色 A/i.test(text)) return '年轻女声，普通话标准，声音自然明亮，语气轻松，适合多人对话。'
  if (/男|male|gentleman|boy|角色 B/i.test(text)) return '年轻男声，普通话标准，音色清爽自然，语速中等，适合游戏解说和对话。'
  return index % 2 === 0
    ? '自然中性声，普通话标准，表达清楚，适合短视频对话。'
    : '沉稳男声，普通话标准，声音有辨识度，适合角色对白。'
}

/**
 * 配音配置面板
 */
export function VoiceConfigPanel({ compact = false }: { compact?: boolean }) {
  void compact
  const [profiles, setProfiles] = useState<ApiProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [modelOptions, setModelOptions] = useState<TextModelOption[]>([])
  const [profileForm, setProfileForm] = useState(createProfileForm)
  const [settings, setSettings] = useState<VoiceGenerateSettings>(() => createDefaultSettings())
  const [voice, setVoice] = useState('alloy')
  const [customVoice, setCustomVoice] = useState('')
  const [automationOptions, setAutomationOptions] = useState(() => loadAutomationPreferences())
  const [previewText, setPreviewText] = useState(DEFAULT_PREVIEW_TEXT)
  const [audioUrl, setAudioUrl] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isLocalSpeaking, setIsLocalSpeaking] = useState(false)
  const [isLoadingVoices, setIsLoadingVoices] = useState(false)
  const [isAudioPlaying, setIsAudioPlaying] = useState(false)
  const [audioDuration, setAudioDuration] = useState(0)
  const [audioCurrentTime, setAudioCurrentTime] = useState(0)
  const [showApiKey, setShowApiKey] = useState(false)
  const [notice, setNotice] = useState<PanelNotice>(null)
  const { addLog } = useTaskStore()
  const profileRequestRef = useRef(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const currentVoiceRef = useRef(voice)
  const lastXiaomiPresetSyncRef = useRef('')

  const activeProvider = profileForm.provider_type
  const activeProviderMeta = VOICE_PROVIDERS.find((p) => p.id === activeProvider) || VOICE_PROVIDERS[0]
  const selectedVoice = customVoice.trim() || voice
  const activeModel = profileForm.custom_model.trim() || profileForm.model
  const isXiaomiMiMo = activeProvider === 'xiaomi_mimo_tts'
  const isXiaomiVoiceDesign = isXiaomiMiMo && activeModel.toLowerCase().includes('voicedesign')
  const isXiaomiVoiceClone = isXiaomiMiMo && activeModel.toLowerCase().includes('voiceclone')
  const supportsMiniMaxAdvanced = activeProvider === 'minimax_tts'
  const supportsStylePrompt = activeProvider === 'openai_tts' || activeProvider === 'gemini_tts' || activeProvider === 'xiaomi_mimo_tts' || activeProvider === 'custom_tts'
  const activeVoiceValue = (() => {
    if (isXiaomiVoiceDesign) {
      const prompt = settings.xiaomi_voice_design_prompt?.trim() || decodeXiaomiVoiceDesign(selectedVoice)
      return encodeXiaomiVoiceDesign(prompt)
    }
    if (isXiaomiVoiceClone) {
      const samplePath = settings.xiaomi_voice_clone_audio_path?.trim() || decodeXiaomiVoiceClonePath(selectedVoice)
      return encodeXiaomiVoiceClonePath(samplePath)
    }
    return selectedVoice
  })()
  const selectedVoiceLabel = voiceValueLabel(activeVoiceValue, voices)

  const voiceSettingsForRequest = (overrides: Partial<VoiceGenerateSettings> = {}): VoiceGenerateSettings => ({
    ...settings,
    ...overrides,
    // 手动语速已下线：逐条配音保持自然语速，分组模式由后端按时间窗临时自适应。
    speed: 1,
  })

  const loadSavedApiKey = async (profileId: number) => {
    const result = await profileApi.getVoiceSecret(profileId)
    return result.api_key
  }

  const selectProfile = async (profile: ApiProfile) => {
    // 记录本次切换请求，避免用户快速切换配置时旧请求把密钥覆盖回来。
    const requestId = ++profileRequestRef.current
    setSelectedProfileId(profile.id)
    setAutomationOptions(saveAutomationPreferences({ voice_profile_id: profile.id }))
    setShowApiKey(false)
    try {
      const apiKey = await loadSavedApiKey(profile.id)
      if (requestId !== profileRequestRef.current) return
      setProfileForm({ name: profile.name, provider_type: profile.provider_type, base_url: profile.base_url, api_key: apiKey, model: profile.model || '', custom_model: '' })
    } catch (error) {
      if (requestId !== profileRequestRef.current) return
      setProfileForm({ name: profile.name, provider_type: profile.provider_type, base_url: profile.base_url, api_key: '', model: profile.model || '', custom_model: '' })
      setNotice({ type: 'warning', message: `读取已保存配音 API Key 失败：${error instanceof Error ? error.message : '未知错误'}` })
    }
    if (profile.extra_params) {
      try {
        const parsed = JSON.parse(profile.extra_params)
        const savedVoice = String(parsed.voice || '').trim()
        setSettings({ ...createDefaultSettings(), ...parsed, speed: 1 })
        if (profile.provider_type === 'custom_tts') {
          setVoice('custom')
          setCustomVoice(savedVoice)
        } else if (savedVoice) {
          setVoice(savedVoice)
          setCustomVoice('')
        }
      } catch { setSettings(createDefaultSettings()) }
    } else {
      setSettings(createDefaultSettings())
      setCustomVoice('')
    }
    setModelOptions(profile.model ? [{ id: profile.model, label: profile.model, owned_by: profile.provider_type }] : [])
    setNotice({ type: 'info', message: `已选择配音配置：${profile.name}` })
  }

  const loadProfiles = async (preferredProfileId?: number) => {
    try {
      const data = await profileApi.listVoice()
      setProfiles(data)
      const target = preferredProfileId
        ? data.find((item) => item.id === preferredProfileId) || null
        : selectedProfileId === null
          ? data[0] || null
          : null
      if (target) await selectProfile(target)
    } catch (error) {
      addLog('error', `加载配音配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => { loadProfiles() }, [])
  useEffect(() => { loadVoices(activeProvider, activeModel) }, [activeProvider, activeModel])
  useEffect(() => { currentVoiceRef.current = voice }, [voice])

  useEffect(() => {
    if (isXiaomiVoiceDesign) {
      const catalogVoice = voices.find((item) => item.id.startsWith('voice_design:'))?.id || 'voice_design'
      const catalogPrompt = decodeXiaomiVoiceDesign(catalogVoice)
      setCustomVoice('')
      setVoice((current) => current.startsWith('voice_design') ? current : catalogVoice)
      setSettings((current) => {
        if ((current.xiaomi_voice_design_prompt || '').trim()) return current
        const prompt = decodeXiaomiVoiceDesign(currentVoiceRef.current) || catalogPrompt
        return prompt ? { ...current, xiaomi_voice_design_prompt: prompt } : current
      })
    } else if (isXiaomiVoiceClone) {
      setCustomVoice('')
      setVoice((current) => current.startsWith('voice_clone') ? current : 'voice_clone')
      setSettings((current) => {
        if ((current.xiaomi_voice_clone_audio_path || '').trim()) return current
        const samplePath = decodeXiaomiVoiceClonePath(currentVoiceRef.current)
        return samplePath ? { ...current, xiaomi_voice_clone_audio_path: samplePath, xiaomi_voice_clone_audio_name: basenameFromPath(samplePath) } : current
      })
    }
  }, [isXiaomiVoiceDesign, isXiaomiVoiceClone, voices])

  useEffect(() => {
    return () => {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  useEffect(() => {
    setIsAudioPlaying(false)
    setAudioDuration(0)
    setAudioCurrentTime(0)
  }, [audioUrl])

  const createNewProfile = () => {
    profileRequestRef.current += 1
    setSelectedProfileId(null)
    setProfileForm(createProfileForm())
    setSettings(createDefaultSettings())
    setVoice('alloy')
    setCustomVoice('')
    setModelOptions([])
    setShowApiKey(false)
    setNotice({ type: 'info', message: '已进入新建配音配置模式' })
  }

  const updateProvider = (providerType: string) => {
    const provider = VOICE_PROVIDERS.find((item) => item.id === providerType) || VOICE_PROVIDERS[0]
    setProfileForm((current) => {
      const currentBaseUrl = current.base_url.trim()
      const shouldUseProviderDefault = !currentBaseUrl || VOICE_PROVIDER_DEFAULT_BASE_URLS.includes(currentBaseUrl)
      return {
        ...current,
        provider_type: provider.id,
        // 用户填了 NewAPI 这类自定义网关地址时，切换渠道不能强行覆盖成官方地址。
        base_url: shouldUseProviderDefault ? (provider.baseUrl || current.base_url) : current.base_url,
        model: provider.model || current.model,
        custom_model: '',
        name: current.name || provider.name,
      }
    })
    setModelOptions(provider.model ? [{ id: provider.model, label: provider.model, owned_by: provider.id }] : [])
    setNotice({ type: 'info', message: `已切换到 ${provider.name}，请确认模型、音色和密钥。` })
  }

  const handleLoadModels = async () => {
    if (!profileForm.base_url.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL' }); return }
    setIsLoadingModels(true)
    setNotice({ type: 'info', message: '正在获取配音模型列表...' })
    try {
      const result = await profileApi.listVoiceModels({ provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, profile_id: selectedProfileId })
      setModelOptions(result.models)
      if (!profileForm.model && result.models.length > 0) setProfileForm((current) => ({ ...current, model: result.models[0].id }))
      setNotice({ type: result.source === 'remote' ? 'success' : 'warning', message: result.message })
    } catch (error) {
      const message = `获取配音模型失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsLoadingModels(false) }
  }

  const loadVoices = async (providerType: string, model?: string) => {
    setIsLoadingVoices(true)
    try {
      const result = await voiceApi.voices(providerType, model)
      setVoices(result.voices)
      if (providerType === 'custom_tts') {
        // 自定义 OpenAI 兼容渠道要保留用户填写的 voice id，不能用占位音色覆盖。
        setVoice((current) => current || result.voices[0]?.id || 'custom')
      } else if (result.voices.length > 0) {
        const normalizedModel = String(model || '').toLowerCase()
        setVoice((current) => {
          if (normalizedModel.includes('voicedesign') && current.startsWith('voice_design')) return current
          if (normalizedModel.includes('voiceclone') && current.startsWith('voice_clone')) return current
          return result.voices.some((item) => item.id === current) ? current : result.voices[0].id
        })
      }
      setNotice({ type: 'success', message: `已获取 ${result.voices.length} 个音色` })
    } catch (error) {
      const message = `获取音色失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsLoadingVoices(false) }
  }

  const handleSaveProfile = async () => {
    if (!profileForm.name.trim() || !profileForm.base_url.trim()) { setNotice({ type: 'warning', message: '请填写配音配置名称和 Base URL' }); return }
    if (!activeModel.trim()) { setNotice({ type: 'warning', message: '请选择或填写配音模型' }); return }
    if (!selectedProfileId && !profileForm.api_key.trim()) { setNotice({ type: 'warning', message: '新建配音配置需要填写 API Key' }); return }
    setIsSaving(true)
    setNotice({ type: 'info', message: '正在保存配音配置...' })
    try {
      const payload = { name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: JSON.stringify({ ...voiceSettingsForRequest(), voice: activeVoiceValue }) }
      const saved = selectedProfileId ? await profileApi.updateVoice(selectedProfileId, payload) : await profileApi.createVoice({ ...payload, api_key: profileForm.api_key })
      setAutomationOptions(saveAutomationPreferences({ voice_profile_id: saved.id }))
      setNotice({ type: 'success', message: `配音配置 "${saved.name}" 已保存` })
      addLog('info', `配音配置 "${saved.name}" 已保存`)
      await loadProfiles(saved.id)
    } catch (error) {
      const message = `保存配音配置失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsSaving(false) }
  }

  const handleTestProfile = async () => {
    if (!profileForm.base_url.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL' }); return }
    if (!activeModel.trim()) { setNotice({ type: 'warning', message: '请选择或填写配音模型' }); return }
    setIsTesting(true)
    setNotice({ type: 'info', message: '正在生成短试听并测试连接...' })
    try {
      const result = await profileApi.testVoiceForm({ name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: JSON.stringify({ ...voiceSettingsForRequest(), voice: activeVoiceValue }), profile_id: selectedProfileId })
      setNotice({ type: 'success', message: result.message }); addLog('info', result.message)
    } catch (error) {
      const message = `测试配音 API 失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsTesting(false) }
  }

  const handlePreview = async () => {
    if (!profileForm.base_url.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL' }); return }
    if (!activeModel.trim()) { setNotice({ type: 'warning', message: '请选择或填写配音模型' }); return }
    if (!previewText.trim()) { setNotice({ type: 'warning', message: '请输入试听文本' }); return }
    setIsGenerating(true)
    setAudioUrl('')
    setNotice({ type: 'info', message: '正在生成试听音频...' })
    try {
      const result = await voiceApi.preview({ text: previewText, profile_id: selectedProfileId, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, voice: activeVoiceValue, model: activeModel, settings: voiceSettingsForRequest() })
      setAudioUrl(`${BASE_URL}${result.audio_url}`)
      setNotice({ type: 'success', message: '试听音频已生成，可直接播放。' })
    } catch (error) {
      const message = `试听失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsGenerating(false) }
  }

  const handleXiaomiCloneSampleChange = async (file: File | null, onSaved?: (path: string, filename: string) => void) => {
    if (!file) return
    const extension = file.name.toLowerCase().split('.').pop() || ''
    if (!['mp3', 'wav'].includes(extension)) {
      setNotice({ type: 'warning', message: '小米音色克隆只支持 mp3 或 wav 样本' })
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setNotice({ type: 'warning', message: '小米音色克隆样本不能超过 10MB' })
      return
    }
    setIsGenerating(true)
    setNotice({ type: 'info', message: '正在保存音色克隆样本...' })
    try {
      const dataUri = await readFileAsDataUri(file)
      const result = await voiceApi.saveXiaomiVoiceCloneSample({ filename: file.name, data_uri: dataUri })
      if (onSaved) {
        onSaved(result.path, file.name)
      } else {
        setSettings((current) => ({
          ...current,
          xiaomi_voice_clone_audio_path: result.path,
          xiaomi_voice_clone_audio_name: file.name,
        }))
        setVoice(encodeXiaomiVoiceClonePath(result.path))
        setCustomVoice('')
      }
      setNotice({ type: 'success', message: `已保存参考音频：${file.name}` })
    } catch (error) {
      const message = `保存参考音频失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsGenerating(false)
    }
  }

  const selectCatalogVoice = (item: VoiceOption) => {
    setVoice(item.id)
    setCustomVoice('')
    if (isXiaomiVoiceDesign) {
      const prompt = decodeXiaomiVoiceDesign(item.id)
      if (prompt) setSettings((current) => ({ ...current, xiaomi_voice_design_prompt: prompt }))
    }
  }

  const handleLocalPreview = () => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      setNotice({ type: 'warning', message: '当前环境不支持浏览器内置试听' })
      return
    }
    if (isLocalSpeaking) {
      window.speechSynthesis.cancel()
      setIsLocalSpeaking(false)
      setNotice({ type: 'info', message: '已停止本地试听' })
      return
    }
    if (!previewText.trim()) {
      setNotice({ type: 'warning', message: '请输入试听文本' })
      return
    }

    const utterance = new SpeechSynthesisUtterance(previewText.trim())
    // 本地试听只用于快速听文本节奏，不消耗任何 TTS API 额度。
    utterance.rate = 1
    utterance.volume = Math.max(0, Math.min(1, Number(settings.volume) > 1 ? 1 : Number(settings.volume) || 1))
    utterance.pitch = 1
    utterance.lang = /[\u3040-\u30ff]/.test(previewText) ? 'ja-JP' : /[A-Za-z]/.test(previewText) && !/[\u4e00-\u9fff]/.test(previewText) ? 'en-US' : 'zh-CN'
    utterance.onend = () => setIsLocalSpeaking(false)
    utterance.onerror = () => {
      setIsLocalSpeaking(false)
      setNotice({ type: 'error', message: '本地试听播放失败，请检查系统语音组件' })
    }
    window.speechSynthesis.cancel()
    setIsLocalSpeaking(true)
    setNotice({ type: 'info', message: '正在使用系统内置语音试听，不会调用配音 API' })
    window.speechSynthesis.speak(utterance)
  }

  const handleSpeakerPreview = async (speaker: VoiceSpeakerProfile) => {
    if (!profileForm.base_url.trim() || !activeModel.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL 和模型' }); return }
    setIsGenerating(true)
    setAudioUrl('')
    setNotice({ type: 'info', message: `正在生成 ${speaker.label} 的试听...` })
    try {
      const result = await voiceApi.preview({
        text: speaker.sample_text || `${speaker.label} 的配音试听。`,
        profile_id: selectedProfileId,
        provider_type: profileForm.provider_type,
        base_url: profileForm.base_url,
        api_key: profileForm.api_key || undefined,
        voice: speaker.voice,
        model: activeModel,
        settings: voiceSettingsForRequest({ style_prompt: speaker.style_prompt || settings.style_prompt }),
      })
      setAudioUrl(`${BASE_URL}${result.audio_url}`)
      setNotice({ type: 'success', message: `说话人 "${speaker.label}" 试听已生成。` })
    } catch (error) {
      const message = `说话人试听失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsGenerating(false) }
  }

  const handleVoicePresetPreview = async (preset: VoicePresetProfile) => {
    if (!profileForm.base_url.trim() || !activeModel.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL 和模型' }); return }
    setIsGenerating(true)
    setAudioUrl('')
    setNotice({ type: 'info', message: `正在生成 ${preset.name} 的试听...` })
    try {
      const result = await voiceApi.preview({
        text: preset.sample_text || `${preset.name} 的配音试听。`,
        profile_id: selectedProfileId,
        provider_type: profileForm.provider_type,
        base_url: profileForm.base_url,
        api_key: profileForm.api_key || undefined,
        voice: preset.voice,
        model: activeModel,
        settings: voiceSettingsForRequest({ style_prompt: preset.style_prompt || settings.style_prompt }),
      })
      setAudioUrl(`${BASE_URL}${result.audio_url}`)
      setNotice({ type: 'success', message: `音色预设 "${preset.name}" 试听已生成。` })
    } catch (error) {
      const message = `音色预设试听失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsGenerating(false) }
  }

  const togglePreviewPlayback = () => {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      void audio.play()
    } else {
      audio.pause()
    }
  }

  const seekPreviewAudio = (value: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(value)) return
    audio.currentTime = Math.max(0, Math.min(value, audio.duration || value))
    setAudioCurrentTime(audio.currentTime)
  }

  const updateSetting = <K extends keyof VoiceGenerateSettings>(key: K, value: VoiceGenerateSettings[K]) => setSettings((current) => ({ ...current, [key]: value }))

  const updateSpeaker = (id: string, patch: Partial<VoiceSpeakerProfile>) => {
    setAutomationOptions(saveAutomationPreferences({ voice_speakers: automationOptions.voice_speakers.map((s) => (s.id === id ? { ...s, ...patch } : s)) }))
  }

  const updateVoicePreset = (id: string, patch: Partial<VoicePresetProfile>) => {
    const presets = automationOptions.voice_presets.map((preset) => (preset.id === id ? { ...preset, ...patch } : preset))
    setAutomationOptions(saveAutomationPreferences({
      voice_presets: presets,
      voice_speakers: automationOptions.voice_speakers.map((speaker) => (
        speaker.preset_id === id
          ? {
              ...speaker,
              voice: patch.voice ?? speaker.voice,
              style_prompt: patch.style_prompt ?? speaker.style_prompt,
              sample_text: patch.sample_text ?? speaker.sample_text,
            }
          : speaker
      )),
    }))
  }

  const syncXiaomiPresetVoices = (showNotice = true) => {
    if (!isXiaomiMiMo) return
    const validVoiceIds = new Set(voices.map((item) => item.id))
    let changed = false

    const markVoiceChange = (currentVoice: string, nextVoice: string) => {
      if (nextVoice !== currentVoice) changed = true
      return nextVoice
    }

    const normalizeVoice = (currentVoice: string, label: string, style: string, index: number) => {
      const voiceValue = String(currentVoice || '').trim()
      if (isXiaomiVoiceDesign) {
        if (voiceValue.startsWith('voice_design:')) return voiceValue
        return markVoiceChange(voiceValue, encodeXiaomiVoiceDesign(defaultXiaomiDesignPrompt(label, style, index)))
      }
      if (isXiaomiVoiceClone) {
        if (voiceValue.startsWith('voice_clone')) return voiceValue
        return markVoiceChange(voiceValue, 'voice_clone')
      }
      if (validVoiceIds.has(voiceValue)) return voiceValue
      const preferredVoice = defaultXiaomiBuiltinVoice(label, style, index)
      // 当前模型的音色目录可能和旧缓存不一致，兜底必须落到已加载目录，避免反复写入同一个无效值。
      const fallbackVoice = validVoiceIds.has(preferredVoice) ? preferredVoice : (voices[0]?.id || 'mimo_default')
      return markVoiceChange(voiceValue, fallbackVoice)
    }

    const nextPresets = automationOptions.voice_presets.map((preset, index) => ({
      ...preset,
      voice: normalizeVoice(preset.voice, preset.name, preset.style_prompt, index),
    }))
    const presetById = new Map(nextPresets.map((preset) => [preset.id, preset]))
    const nextSpeakers = automationOptions.voice_speakers.map((speaker, index) => {
      const preset = speaker.preset_id ? presetById.get(speaker.preset_id) : null
      if (preset) {
        const nextSpeaker = {
          ...speaker,
          voice: preset.voice,
          style_prompt: preset.style_prompt,
          sample_text: speaker.sample_text || preset.sample_text,
        }
        if (nextSpeaker.voice !== speaker.voice || nextSpeaker.style_prompt !== speaker.style_prompt) changed = true
        if (nextSpeaker.sample_text !== speaker.sample_text) changed = true
        return nextSpeaker
      }
      return {
        ...speaker,
        voice: normalizeVoice(speaker.voice, speaker.label, speaker.style_prompt || '', index),
      }
    })

    if (!changed) return
    setAutomationOptions(saveAutomationPreferences({ voice_presets: nextPresets, voice_speakers: nextSpeakers }))
    if (showNotice) setNotice({ type: 'success', message: '已按当前小米模型同步音色预设和多人说话人' })
  }

  const voiceCatalogSignature = voices.map((item) => item.id).join('\u0001')

  useEffect(() => {
    if (!isXiaomiMiMo || !voiceCatalogSignature) return
    const syncKey = `${activeProvider}\u0001${activeModel}\u0001${voiceCatalogSignature}`
    if (lastXiaomiPresetSyncRef.current === syncKey) return
    lastXiaomiPresetSyncRef.current = syncKey
    syncXiaomiPresetVoices(false)
  }, [isXiaomiMiMo, activeProvider, activeModel, voiceCatalogSignature])

  const addVoicePreset = () => {
    const nextIndex = automationOptions.voice_presets.length + 1
    const preset: VoicePresetProfile = {
      id: `voice_preset_${Date.now()}`,
      name: `音色 ${nextIndex}`,
      voice: activeVoiceValue || 'Kore',
      style_prompt: settings.style_prompt || '',
      sample_text: previewText || DEFAULT_PREVIEW_TEXT,
    }
    setAutomationOptions(saveAutomationPreferences({ voice_presets: [...automationOptions.voice_presets, preset] }))
    setNotice({ type: 'success', message: `已添加音色预设：${preset.name}` })
  }

  const saveCurrentAsVoicePreset = () => {
    const nextIndex = automationOptions.voice_presets.length + 1
    const preset: VoicePresetProfile = {
      id: `voice_preset_${Date.now()}`,
      name: `当前音色 ${nextIndex}`,
      voice: activeVoiceValue,
      style_prompt: settings.style_prompt,
      sample_text: previewText || DEFAULT_PREVIEW_TEXT,
    }
    setAutomationOptions(saveAutomationPreferences({ voice_presets: [...automationOptions.voice_presets, preset] }))
    setNotice({ type: 'success', message: `已把当前音色保存为预设：${preset.name}` })
  }

  const removeVoicePreset = (id: string) => {
    if (automationOptions.voice_presets.length <= 1) {
      setNotice({ type: 'warning', message: '至少保留一个音色预设' })
      return
    }
    const preset = automationOptions.voice_presets.find((item) => item.id === id)
    const nextPresets = automationOptions.voice_presets.filter((item) => item.id !== id)
    const fallback = nextPresets[0]
    setAutomationOptions(saveAutomationPreferences({
      voice_presets: nextPresets,
      voice_speakers: automationOptions.voice_speakers.map((speaker) => (
        speaker.preset_id === id
          ? { ...speaker, preset_id: fallback.id, voice: fallback.voice, style_prompt: fallback.style_prompt, sample_text: speaker.sample_text || fallback.sample_text }
          : speaker
      )),
    }))
    setNotice({ type: 'info', message: `已删除音色预设：${preset?.name || id}` })
  }

  const applyPresetToSpeaker = (speakerId: string, presetId: string) => {
    const preset = automationOptions.voice_presets.find((item) => item.id === presetId)
    if (!preset) return
    updateSpeaker(speakerId, {
      preset_id: preset.id,
      voice: preset.voice,
      style_prompt: preset.style_prompt,
      sample_text: preset.sample_text,
    })
  }

  const addSpeaker = () => {
    const nextIndex = automationOptions.voice_speakers.length + 1
    const preset = automationOptions.voice_presets[(nextIndex - 1) % Math.max(automationOptions.voice_presets.length, 1)]
    const nextSpeaker: VoiceSpeakerProfile = {
      id: `speaker_${Date.now()}`,
      label: `角色 ${nextIndex}`,
      preset_id: preset?.id || '',
      voice: preset?.voice || activeVoiceValue,
      style_prompt: preset?.style_prompt || '',
      sample_text: preset?.sample_text || `这是角色 ${nextIndex} 的一句对话试听。`,
    }
    setAutomationOptions(saveAutomationPreferences({ multi_speaker_enabled: true, voice_speakers: [...automationOptions.voice_speakers, nextSpeaker] }))
  }
  const removeSpeaker = (id: string) => {
    const next = automationOptions.voice_speakers.filter((s) => s.id !== id)
    if (next.length === 0) { addLog('warn', '至少保留一个说话人'); return }
    setAutomationOptions(saveAutomationPreferences({ voice_speakers: next }))
  }

  const modelOpts: FieldOption[] = (() => {
    const opts = modelOptions.map((m) => [m.id, m.label === m.id ? m.id : `${m.label} · ${m.id}`] as FieldOption)
    if (profileForm.model && !opts.some(([v]) => v === profileForm.model)) opts.unshift([profileForm.model, profileForm.model])
    return opts
  })()

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">配音配置</h2>
        <p className="text-sm text-muted-foreground">配置配音渠道与音色、试听确认；生成参数和多说话人映射在高级里。配音默认关闭，需在「一键策略」开启。</p>
      </div>

      {/* 已保存配音渠道 */}
      <div className="flex flex-wrap items-center gap-2">
        {profiles.map((profile) => (
          <button
            key={profile.id}
            onClick={() => selectProfile(profile)}
            className={cn('rounded-lg border px-3 py-2 text-left text-sm transition-colors', selectedProfileId === profile.id ? 'border-primary bg-primary/10' : 'bg-card hover:border-primary/50')}
          >
            <span className="block font-medium">{profile.name}</span>
            <span className="block text-xs text-muted-foreground">{providerLabel(profile.provider_type)}</span>
          </button>
        ))}
        <Button variant="outline" size="sm" onClick={createNewProfile}>+ 新建渠道</Button>
      </div>

      {/* 常用：渠道与密钥 */}
      <Card>
        <CardHeader><CardTitle className="text-sm">渠道与密钥</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <NoticeBox notice={notice} />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="配置名称" value={profileForm.name} onChange={(v) => setProfileForm({ ...profileForm, name: v })} />
            <SelectField label="渠道" value={profileForm.provider_type} options={VOICE_PROVIDERS.map((p) => [p.id, p.name] as FieldOption)} onChange={updateProvider} />
            <TextField label="Base URL" value={profileForm.base_url} onChange={(v) => setProfileForm({ ...profileForm, base_url: v })} />
            <SecretField
              label="API Key"
              value={profileForm.api_key}
              placeholder={selectedProfileId ? '已保存密钥' : '请输入 API Key'}
              description={selectedProfileId ? '已保存配置会自动带出密钥，默认隐藏；点击右侧眼睛可查看。' : undefined}
              visible={showApiKey}
              maskWhenHidden={Boolean(selectedProfileId)}
              onToggleVisible={() => setShowApiKey((current) => !current)}
              onChange={(v) => setProfileForm({ ...profileForm, api_key: v })}
            />
            <SelectField label="模型" value={profileForm.model} options={modelOpts} placeholder="先获取模型或填写自定义模型" onChange={(v) => setProfileForm({ ...profileForm, model: v, custom_model: '' })} />
            <TextField label="自定义模型" value={profileForm.custom_model} placeholder={profileForm.model || activeProviderMeta.model || '例如 gpt-4o-mini-tts'} onChange={(v) => setProfileForm({ ...profileForm, custom_model: v })} />
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button onClick={handleSaveProfile} disabled={isSaving}>{isSaving ? '保存中…' : '保存配置'}</Button>
            <Button variant="outline" onClick={handleLoadModels} disabled={isLoadingModels}>{isLoadingModels ? '获取中…' : '获取模型'}</Button>
            <Button variant="outline" onClick={handleTestProfile} disabled={isTesting}>{isTesting ? '测试中…' : '测试连接'}</Button>
            <Button variant="outline" onClick={() => loadVoices(activeProvider, activeModel)}>{isLoadingVoices ? '获取中…' : '获取音色'}</Button>
            <span className="text-xs text-muted-foreground">当前模型：{activeModel || '未选择'}</span>
          </div>
        </CardContent>
      </Card>

      {/* 音色与试听 */}
      <Card>
        <CardHeader><CardTitle className="text-sm">音色与试听</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2">
            {voices.map((item) => (
              <button
                key={item.id}
                onClick={() => selectCatalogVoice(item)}
                className={cn('rounded-md border p-2.5 text-left transition-colors', selectedVoice === item.id ? 'border-primary bg-primary/10' : 'bg-card hover:border-primary/50')}
              >
                <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                  <span className="truncate">{item.name}</span>
                  <span className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">{voiceGenderLabel(item.gender)}</span>
                </span>
                <span className="block truncate text-xs text-muted-foreground">{item.language} · {item.style}</span>
              </button>
            ))}
          </div>
          {isXiaomiVoiceDesign ? (
            <TextareaField
              label="文字定制音色描述"
              value={settings.xiaomi_voice_design_prompt || ''}
              rows={3}
              placeholder="例如：年轻男声，普通话标准，音色清爽自然，语速中等，适合游戏解说和对话。"
              onChange={(v) => updateSetting('xiaomi_voice_design_prompt', v)}
            />
          ) : isXiaomiVoiceClone ? (
            <div className="space-y-2 rounded-lg border p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <TextField
                  label="克隆样本路径"
                  value={settings.xiaomi_voice_clone_audio_path || ''}
                  placeholder="选择 mp3/wav 后自动填入，也可以手动填写本地路径"
                  onChange={(v) => updateSetting('xiaomi_voice_clone_audio_path', v)}
                />
                <div className="space-y-1.5">
                  <label className="text-sm font-normal">上传参考音频</label>
                  <input
                    type="file"
                    accept=".mp3,.wav,audio/mpeg,audio/wav"
                    className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm"
                    onChange={(event) => {
                      const file = event.target.files?.[0] || null
                      void handleXiaomiCloneSampleChange(file)
                      event.currentTarget.value = ''
                    }}
                  />
                  <p className="text-xs text-muted-foreground">{settings.xiaomi_voice_clone_audio_name || '支持 mp3/wav，最大 10MB'}</p>
                </div>
              </div>
            </div>
          ) : (
            <TextField label="自定义 voice id" value={customVoice} placeholder={voice} onChange={setCustomVoice} />
          )}
          <TextareaField label="试听文本" value={previewText} rows={3} onChange={setPreviewText} />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={handleLocalPreview} disabled={!previewText.trim()}>{isLocalSpeaking ? '停止本地试听' : '本地试听'}</Button>
            <Button onClick={handlePreview} disabled={isGenerating || !previewText.trim()}>{isGenerating ? '生成中…' : '生成试听'}</Button>
            <Button variant="outline" onClick={saveCurrentAsVoicePreset} disabled={!activeVoiceValue.trim()}>保存为预设</Button>
            <span className="text-xs text-muted-foreground">当前音色：{selectedVoiceLabel}</span>
          </div>
          {audioUrl && (
            <VoicePreviewPlayer
              audioRef={audioRef}
              audioUrl={audioUrl}
              isPlaying={isAudioPlaying}
              duration={audioDuration}
              currentTime={audioCurrentTime}
              onToggle={togglePreviewPlayback}
              onSeek={seekPreviewAudio}
              onLoadedMetadata={(duration) => setAudioDuration(duration)}
              onTimeUpdate={(currentTime) => setAudioCurrentTime(currentTime)}
              onPlayingChange={setIsAudioPlaying}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">音色预设库</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap justify-end gap-2">
            {isXiaomiMiMo && (
              <Button variant="outline" size="sm" onClick={() => syncXiaomiPresetVoices(true)}>
                同步当前模型音色
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={addVoicePreset}>
              <Plus className="mr-1 size-4" />
              添加音色
            </Button>
          </div>
          {automationOptions.voice_presets.map((preset) => (
            <div key={preset.id} className="space-y-3 rounded-lg border bg-card p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <TextField label="预设名称" value={preset.name} onChange={(v) => updateVoicePreset(preset.id, { name: v })} />
                {isXiaomiVoiceDesign ? (
                  <TextareaField
                    label="文字音色描述"
                    value={editableXiaomiVoiceDesignPrompt(preset.voice)}
                    rows={2}
                    placeholder="例如：低沉男声，普通话标准，声音稳重，适合旁白。"
                    onChange={(v) => updateVoicePreset(preset.id, { voice: encodeXiaomiVoiceDesign(v) })}
                  />
                ) : isXiaomiVoiceClone ? (
                  <div className="space-y-2">
                    <TextField
                      label="克隆样本路径"
                      value={editableXiaomiVoiceClonePath(preset.voice)}
                      placeholder="选择 mp3/wav 后自动填入"
                      onChange={(v) => updateVoicePreset(preset.id, { voice: encodeXiaomiVoiceClonePath(v) })}
                    />
                    <input
                      type="file"
                      accept=".mp3,.wav,audio/mpeg,audio/wav"
                      className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm"
                      onChange={(event) => {
                        const file = event.target.files?.[0] || null
                        void handleXiaomiCloneSampleChange(file, (path) => updateVoicePreset(preset.id, { voice: encodeXiaomiVoiceClonePath(path) }))
                        event.currentTarget.value = ''
                      }}
                    />
                  </div>
                ) : activeProvider === 'custom_tts' ? (
                  <TextField label="voice id" value={preset.voice} placeholder="例如 alloy、nova 或服务商自定义 voice" onChange={(v) => updateVoicePreset(preset.id, { voice: v })} />
                ) : (
                  <SelectField
                    label="voice id"
                    value={preset.voice}
                    options={voiceOptionsWithCustom(voices, automationOptions.voice_presets, preset.voice)}
                    onChange={(v) => updateVoicePreset(preset.id, { voice: v })}
                  />
                )}
              </div>
              <TextareaField label="风格提示" value={preset.style_prompt} rows={2} placeholder="例如：用年轻自然的女声，语气轻松，适合对话。" onChange={(v) => updateVoicePreset(preset.id, { style_prompt: v })} />
              <TextField label="试听文本" value={preset.sample_text} onChange={(v) => updateVoicePreset(preset.id, { sample_text: v })} />
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => handleVoicePresetPreview(preset)} disabled={isGenerating}>试听</Button>
                <Button variant="outline" size="sm" className="text-destructive" onClick={() => removeVoicePreset(preset.id)} disabled={automationOptions.voice_presets.length <= 1}>
                  <Trash2 className="mr-1 size-4" />
                  删除
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* 高级 */}
      <Accordion type="multiple" className="space-y-2">
        <AccordionItem value="params" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">配音参数（音量 / 格式 / 重试等）</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SliderField label="音量" value={settings.volume} min={0.1} max={10} step={0.1} format={(v) => v.toFixed(1)} onChange={(v) => updateSetting('volume', v)} />
            <SliderField label="音调" value={settings.pitch} min={-12} max={12} step={1} onChange={(v) => updateSetting('pitch', v)} />
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField label="格式" value={settings.format} options={FORMAT_OPTIONS} onChange={(v) => updateSetting('format', v as VoiceGenerateSettings['format'])} />
              <SelectField label="采样率" value={String(settings.sample_rate)} options={SAMPLE_RATE_OPTIONS} onChange={(v) => updateSetting('sample_rate', Number(v))} />
              <SelectField label="码率" value={String(settings.bitrate)} options={BITRATE_OPTIONS} onChange={(v) => updateSetting('bitrate', Number(v))} />
              <SelectField label="声道" value={String(settings.channel)} options={CHANNEL_OPTIONS} onChange={(v) => updateSetting('channel', Number(v))} />
              <NumberField label="失败重试" value={settings.retry_count} min={0} max={10} step={1} suffix="次" onChange={(v) => updateSetting('retry_count', Math.max(0, Math.round(v)))} />
              <NumberField label="重试间隔" value={settings.retry_interval_ms} min={0} max={30000} step={100} suffix="ms" onChange={(v) => updateSetting('retry_interval_ms', Math.max(0, Math.round(v)))} />
            </div>
            {supportsMiniMaxAdvanced && (
              <div className="grid gap-3 sm:grid-cols-2">
                <SelectField label="情绪" value={settings.emotion} options={EMOTION_OPTIONS} onChange={(v) => updateSetting('emotion', v)} />
                <SelectField label="语言增强" value={settings.language_boost} options={LANG_BOOST_OPTIONS} onChange={(v) => updateSetting('language_boost', v)} />
                <SliderField label="强度" value={settings.intensity} min={-100} max={100} step={1} onChange={(v) => updateSetting('intensity', v)} />
                <SliderField label="音色质感" value={settings.timbre} min={-100} max={100} step={1} onChange={(v) => updateSetting('timbre', v)} />
              </div>
            )}
            {supportsStylePrompt && (
              <TextareaField label="风格提示" value={settings.style_prompt} rows={3} placeholder="例如：用自然口播风格，语气稳定，适合短视频解说。" onChange={(v) => updateSetting('style_prompt', v)} />
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="auto" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">一键配音策略</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SwitchField label="一键流程启用配音" description="关闭时一键完成会跳过配音" checked={automationOptions.enable_voice} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ enable_voice: v }))} />
            <SwitchField label="额外导出无配音字幕版" description="开启后会多生成一个保留原声、没有配音的字幕版视频" checked={automationOptions.export_subtitle_only_when_voice} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ export_subtitle_only_when_voice: v }))} />
            <SelectField label="生成方式" value={automationOptions.voice_mode} options={[['grouped', '时间轴分组合成（更快，自动调速）'], ['batched', '逐条按时间轴并发生成（最稳）'], ['segmented', '逐条按时间轴串行生成'], ['full', '整段生成（不保证同步）']]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_mode: v as typeof automationOptions.voice_mode }))} />
            {automationOptions.voice_mode === 'grouped' && (
              <p className="rounded-md border border-accent/30 bg-accent/10 px-3 py-2 text-xs leading-5 text-accent">
                分组合成会把相邻字幕合成一次请求，按整组时间窗自动判断语速。如果生成音频过长，会自动提速重试，仍超时再拆成更小组。
              </p>
            )}
            {automationOptions.voice_mode === 'grouped' ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <NumberField label="每组最大行数" value={automationOptions.voice_group_size} min={1} max={12} step={1} suffix="行" onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_group_size: Math.max(1, Math.round(v)) }))} />
                <NumberField label="每组最长秒数" value={automationOptions.voice_group_max_seconds} min={1} max={30} step={1} suffix="秒" onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_group_max_seconds: Math.max(1, Math.round(v)) }))} />
                <NumberField label="每组字符上限" value={automationOptions.voice_group_chars} min={80} max={2000} step={20} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_group_chars: Math.max(80, Math.round(v)) }))} />
                <NumberField label="最大合并停顿" value={automationOptions.voice_group_gap_ms} min={0} max={5000} step={100} suffix="ms" onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_group_gap_ms: Math.max(0, Math.round(v)) }))} />
                <NumberField label="并发数" value={automationOptions.voice_concurrency} min={1} max={8} step={1} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_concurrency: Math.max(1, Math.round(v)) }))} />
              </div>
            ) : automationOptions.voice_mode === 'batched' ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <NumberField label="并发数" value={automationOptions.voice_concurrency} min={1} max={8} step={1} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_concurrency: Math.max(1, Math.round(v)) }))} />
              </div>
            ) : null}
            <SwitchField label="自动多人对话" description="字幕出现说话人标签时才按映射选音色，未检测到多人时保持默认音色" checked={automationOptions.multi_speaker_enabled} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ multi_speaker_enabled: v }))} />
            <SelectField label="音频合成" value={automationOptions.audio_mode} options={[['background', '本地 AI 去人声，保留背景声'], ['mix', '混合完整原视频声音'], ['replace', '仅保留配音']]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ audio_mode: v as typeof automationOptions.audio_mode }))} />
            {automationOptions.audio_mode === 'background' && (
              <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning">
                该模式会调用本地 Demucs 分离模型，生成 no_vocals 背景轨后按 1.00 音量叠加配音；未安装本地模型时会停在导出阶段并提示安装。
              </p>
            )}
            {automationOptions.audio_mode !== 'background' && (
              <SliderField label="原声音量" value={automationOptions.original_volume} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ original_volume: v }))} />
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="speakers" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">多人说话人音色（{automationOptions.voice_speakers.length}）</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <div className="flex justify-end">
              <Button variant="outline" size="sm" onClick={addSpeaker}>+ 添加说话人</Button>
            </div>
            {automationOptions.voice_speakers.map((speaker) => (
              <div key={speaker.id} className="space-y-2 rounded-lg border bg-card p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <TextField label="说话人标签" value={speaker.label} onChange={(v) => updateSpeaker(speaker.id, { label: v })} />
                  <SelectField
                    label="音色预设"
                    value={speaker.preset_id || ''}
                    options={[['', '不绑定预设'], ...voicePresetOptions(automationOptions.voice_presets)]}
                    onChange={(v) => v ? applyPresetToSpeaker(speaker.id, v) : updateSpeaker(speaker.id, { preset_id: '' })}
                  />
                  {isXiaomiVoiceDesign ? (
                    <TextareaField
                      label="文字音色描述"
                      value={editableXiaomiVoiceDesignPrompt(speaker.voice)}
                      rows={2}
                      placeholder="例如：年轻女声，语气轻松，适合对话。"
                      onChange={(v) => updateSpeaker(speaker.id, { voice: encodeXiaomiVoiceDesign(v), preset_id: '' })}
                    />
                  ) : isXiaomiVoiceClone ? (
                    <div className="space-y-2">
                      <TextField
                        label="克隆样本路径"
                        value={editableXiaomiVoiceClonePath(speaker.voice)}
                        placeholder="选择 mp3/wav 后自动填入"
                        onChange={(v) => updateSpeaker(speaker.id, { voice: encodeXiaomiVoiceClonePath(v), preset_id: '' })}
                      />
                      <input
                        type="file"
                        accept=".mp3,.wav,audio/mpeg,audio/wav"
                        className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm"
                        onChange={(event) => {
                          const file = event.target.files?.[0] || null
                          void handleXiaomiCloneSampleChange(file, (path) => updateSpeaker(speaker.id, { voice: encodeXiaomiVoiceClonePath(path), preset_id: '' }))
                          event.currentTarget.value = ''
                        }}
                      />
                    </div>
                  ) : activeProvider === 'custom_tts' ? (
                    <TextField label="voice id" value={speaker.voice} placeholder="例如 alloy、nova 或服务商自定义 voice" onChange={(v) => updateSpeaker(speaker.id, { voice: v, preset_id: '' })} />
                  ) : (
                    <SelectField
                      label="voice id"
                      value={speaker.voice}
                      options={voiceOptionsWithCustom(voices, automationOptions.voice_presets, speaker.voice)}
                      onChange={(v) => updateSpeaker(speaker.id, { voice: v, preset_id: '' })}
                    />
                  )}
                </div>
                <TextareaField label="风格提示" value={speaker.style_prompt || ''} rows={2} onChange={(v) => updateSpeaker(speaker.id, { style_prompt: v, preset_id: '' })} />
                <TextField label="试听文本" value={speaker.sample_text} onChange={(v) => updateSpeaker(speaker.id, { sample_text: v })} />
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => handleSpeakerPreview(speaker)} disabled={isGenerating}>试听</Button>
                  <Button variant="outline" size="sm" className="text-destructive" onClick={() => removeSpeaker(speaker.id)}>删除</Button>
                </div>
              </div>
            ))}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

/** 渠道展示名称 */
function providerLabel(providerType: string) {
  return VOICE_PROVIDERS.find((provider) => provider.id === providerType)?.name || providerType
}

/** 把本地参考音频读成 data URI，传给后端保存为克隆样本 */
function readFileAsDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('读取参考音频失败'))
    reader.readAsDataURL(file)
  })
}

/** 面板内反馈提示 */
function NoticeBox({ notice }: { notice: PanelNotice }) {
  if (!notice) return null
  const classes = {
    info: 'border-accent/30 bg-accent/10 text-accent',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    error: 'border-destructive/30 bg-destructive/10 text-destructive',
  }[notice.type]
  return <div className={cn('rounded-md border px-3 py-2 text-xs', classes)} role={notice.type === 'error' ? 'alert' : 'status'}>{notice.message}</div>
}

function VoicePreviewPlayer({
  audioRef,
  audioUrl,
  isPlaying,
  duration,
  currentTime,
  onToggle,
  onSeek,
  onLoadedMetadata,
  onTimeUpdate,
  onPlayingChange,
}: {
  audioRef: MutableRefObject<HTMLAudioElement | null>
  audioUrl: string
  isPlaying: boolean
  duration: number
  currentTime: number
  onToggle: () => void
  onSeek: (value: number) => void
  onLoadedMetadata: (duration: number) => void
  onTimeUpdate: (currentTime: number) => void
  onPlayingChange: (playing: boolean) => void
}) {
  const safeDuration = Number.isFinite(duration) && duration > 0 ? duration : 0
  const safeCurrentTime = Math.min(currentTime, safeDuration || currentTime)

  return (
    <div className="rounded-lg border bg-card/80 p-3">
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        onLoadedMetadata={(event) => onLoadedMetadata(event.currentTarget.duration || 0)}
        onTimeUpdate={(event) => onTimeUpdate(event.currentTarget.currentTime || 0)}
        onPlay={() => onPlayingChange(true)}
        onPause={() => onPlayingChange(false)}
        onEnded={() => onPlayingChange(false)}
      >
        <track kind="captions" />
      </audio>
      <div className="flex items-center gap-3">
        <Button type="button" variant="outline" size="icon-sm" onClick={onToggle} aria-label={isPlaying ? '暂停试听' : '播放试听'}>
          {isPlaying ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
        <div className="min-w-0 flex-1">
          <input
            type="range"
            min={0}
            max={safeDuration || 0}
            step={0.01}
            value={safeDuration ? safeCurrentTime : 0}
            onChange={(event) => onSeek(Number(event.target.value))}
            className="h-2 w-full accent-primary"
            aria-label="试听进度"
          />
          <div className="mt-1 flex items-center justify-between text-xs tabular-nums text-muted-foreground">
            <span>{formatAudioClock(safeCurrentTime)}</span>
            <span>{formatAudioClock(safeDuration)}</span>
          </div>
        </div>
        <Volume2 className="size-4 shrink-0 text-muted-foreground" />
      </div>
    </div>
  )
}

function formatAudioClock(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00'
  const total = Math.floor(seconds)
  const minutes = Math.floor(total / 60)
  const rest = String(total % 60).padStart(2, '0')
  return `${minutes}:${rest}`
}
