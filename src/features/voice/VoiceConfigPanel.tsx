// src/features/voice/VoiceConfigPanel.tsx
// 配音配置面板 - 管理配音 API、音色、试听和生成参数
// 交互重做：渠道/音色/试听露出，生成参数与多说话人收进高级折叠

import { useEffect, useRef, useState } from 'react'
import { profileApi, voiceApi } from '@/lib/api'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { ApiProfile, TextModelOption, VoiceGenerateSettings, VoiceOption, VoiceSpeakerProfile } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { TextField, SecretField, SelectField, SwitchField, SliderField, TextareaField, type FieldOption } from '@/components/fields'

/** 配音渠道配置 */
const VOICE_PROVIDERS = [
  { id: 'openai_tts', name: 'OpenAI TTS', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini-tts' },
  { id: 'gemini_tts', name: 'Gemini TTS', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.5-flash-preview-tts' },
  { id: 'minimax_tts', name: 'MiniMax T2A', baseUrl: 'https://api.minimax.io/v1', model: 'speech-2.8-hd' },
  { id: 'xiaomi_mimo_tts', name: '小米 MiMo TTS', baseUrl: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2.5-tts' },
  { id: 'custom_tts', name: '自定义 OpenAI 兼容', baseUrl: '', model: '' },
]

/** 默认试听文本 */
const DEFAULT_PREVIEW_TEXT = '这是一段配音试听，用来确认音色、语速、音量和情绪是否适合当前视频。'

/** 配音参数默认值 */
function createDefaultSettings(): VoiceGenerateSettings {
  return {
    speed: 1, volume: 1, pitch: 0, format: 'mp3', sample_rate: 32000, bitrate: 128000, channel: 1,
    emotion: '', style_prompt: '', language_boost: 'auto', intensity: 0, timbre: 0, voice_pitch: 0, sound_effects: '',
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
  const [isLoadingVoices, setIsLoadingVoices] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [notice, setNotice] = useState<PanelNotice>(null)
  const { addLog } = useTaskStore()
  const profileRequestRef = useRef(0)

  const activeProvider = profileForm.provider_type
  const activeProviderMeta = VOICE_PROVIDERS.find((p) => p.id === activeProvider) || VOICE_PROVIDERS[0]
  const selectedVoice = customVoice.trim() || voice
  const activeModel = profileForm.custom_model.trim() || profileForm.model
  const supportsMiniMaxAdvanced = activeProvider === 'minimax_tts'
  const supportsStylePrompt = activeProvider === 'openai_tts' || activeProvider === 'gemini_tts' || activeProvider === 'xiaomi_mimo_tts' || activeProvider === 'custom_tts'
  const selectedVoiceLabel = voices.find((item) => item.id === selectedVoice)?.name || selectedVoice

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
        setSettings({ ...createDefaultSettings(), ...parsed })
        if (parsed.voice) setVoice(parsed.voice)
      } catch { setSettings(createDefaultSettings()) }
    } else {
      setSettings(createDefaultSettings())
    }
    setCustomVoice('')
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
  useEffect(() => { loadVoices(activeProvider) }, [activeProvider])

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
    setProfileForm((current) => ({ ...current, provider_type: provider.id, base_url: provider.baseUrl || current.base_url, model: provider.model || current.model, custom_model: '', name: current.name || provider.name }))
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

  const loadVoices = async (providerType: string) => {
    setIsLoadingVoices(true)
    try {
      const result = await voiceApi.voices(providerType)
      setVoices(result.voices)
      if (result.voices.length > 0) setVoice(result.voices[0].id)
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
      const payload = { name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: JSON.stringify({ ...settings, voice: selectedVoice }) }
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
      const result = await profileApi.testVoiceForm({ name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: JSON.stringify({ ...settings, voice: selectedVoice }), profile_id: selectedProfileId })
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
      const result = await voiceApi.preview({ text: previewText, profile_id: selectedProfileId, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, voice: selectedVoice, model: activeModel, settings })
      setAudioUrl(`http://127.0.0.1:8765${result.audio_url}`)
      setNotice({ type: 'success', message: '试听音频已生成，可直接播放。' })
    } catch (error) {
      const message = `试听失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsGenerating(false) }
  }

  const handleSpeakerPreview = async (speaker: VoiceSpeakerProfile) => {
    if (!profileForm.base_url.trim() || !activeModel.trim()) { setNotice({ type: 'warning', message: '请先填写 Base URL 和模型' }); return }
    setIsGenerating(true)
    setAudioUrl('')
    setNotice({ type: 'info', message: `正在生成 ${speaker.label} 的试听...` })
    try {
      const result = await voiceApi.preview({ text: speaker.sample_text || `${speaker.label} 的配音试听。`, profile_id: selectedProfileId, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, voice: speaker.voice, model: activeModel, settings })
      setAudioUrl(`http://127.0.0.1:8765${result.audio_url}`)
      setNotice({ type: 'success', message: `说话人 "${speaker.label}" 试听已生成。` })
    } catch (error) {
      const message = `说话人试听失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message }); addLog('error', message)
    } finally { setIsGenerating(false) }
  }

  const updateSetting = <K extends keyof VoiceGenerateSettings>(key: K, value: VoiceGenerateSettings[K]) => setSettings((current) => ({ ...current, [key]: value }))

  const updateSpeaker = (id: string, patch: Partial<VoiceSpeakerProfile>) => {
    setAutomationOptions(saveAutomationPreferences({ voice_speakers: automationOptions.voice_speakers.map((s) => (s.id === id ? { ...s, ...patch } : s)) }))
  }
  const addSpeaker = () => {
    const nextIndex = automationOptions.voice_speakers.length + 1
    const nextSpeaker: VoiceSpeakerProfile = { id: `speaker_${Date.now()}`, label: `角色 ${nextIndex}`, voice: selectedVoice, sample_text: `这是角色 ${nextIndex} 的一句对话试听。` }
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
            <Button variant="outline" onClick={() => loadVoices(activeProvider)}>{isLoadingVoices ? '获取中…' : '获取音色'}</Button>
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
                onClick={() => { setVoice(item.id); setCustomVoice('') }}
                className={cn('rounded-md border p-2.5 text-left transition-colors', selectedVoice === item.id ? 'border-primary bg-primary/10' : 'bg-card hover:border-primary/50')}
              >
                <span className="block truncate text-sm font-medium">{item.name}</span>
                <span className="block truncate text-xs text-muted-foreground">{item.language} · {item.style}</span>
              </button>
            ))}
          </div>
          <TextField label="自定义 voice id" value={customVoice} placeholder={voice} onChange={setCustomVoice} />
          <TextareaField label="试听文本" value={previewText} rows={3} onChange={setPreviewText} />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={handlePreview} disabled={isGenerating || !previewText.trim()}>{isGenerating ? '生成中…' : '生成试听'}</Button>
            <span className="text-xs text-muted-foreground">当前音色：{selectedVoiceLabel}</span>
          </div>
          {audioUrl && <audio className="w-full" controls src={audioUrl}><track kind="captions" /></audio>}
        </CardContent>
      </Card>

      {/* 高级 */}
      <Accordion type="multiple" className="space-y-2">
        <AccordionItem value="params" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">配音参数（语速 / 音量 / 格式等）</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SliderField label="语速" value={settings.speed} min={0.5} max={2} step={0.05} format={(v) => v.toFixed(2)} suffix="x" onChange={(v) => updateSetting('speed', v)} />
            <SliderField label="音量" value={settings.volume} min={0.1} max={10} step={0.1} format={(v) => v.toFixed(1)} onChange={(v) => updateSetting('volume', v)} />
            <SliderField label="音调" value={settings.pitch} min={-12} max={12} step={1} onChange={(v) => updateSetting('pitch', v)} />
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField label="格式" value={settings.format} options={FORMAT_OPTIONS} onChange={(v) => updateSetting('format', v as VoiceGenerateSettings['format'])} />
              <SelectField label="采样率" value={String(settings.sample_rate)} options={SAMPLE_RATE_OPTIONS} onChange={(v) => updateSetting('sample_rate', Number(v))} />
              <SelectField label="码率" value={String(settings.bitrate)} options={BITRATE_OPTIONS} onChange={(v) => updateSetting('bitrate', Number(v))} />
              <SelectField label="声道" value={String(settings.channel)} options={CHANNEL_OPTIONS} onChange={(v) => updateSetting('channel', Number(v))} />
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
            <SelectField label="生成方式" value={automationOptions.voice_mode} options={[['segmented', '按字幕分段'], ['full', '整段生成']]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ voice_mode: v as typeof automationOptions.voice_mode }))} />
            <SwitchField label="多人对话" description="字幕出现说话人标签时按映射选音色" checked={automationOptions.multi_speaker_enabled} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ multi_speaker_enabled: v, voice_mode: v ? 'segmented' : automationOptions.voice_mode }))} />
            <SelectField label="音频合成" value={automationOptions.audio_mode} options={[['mix', '保留原声并混合'], ['replace', '替换原声']]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ audio_mode: v as typeof automationOptions.audio_mode }))} />
            <SliderField label="原声音量" value={automationOptions.original_volume} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ original_volume: v }))} />
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
                    label="音色"
                    value={speaker.voice}
                    options={[...voices.map((item) => [item.id, `${item.name} · ${item.style}`] as FieldOption), [speaker.voice, speaker.voice] as FieldOption].filter((item, index, list) => list.findIndex((t) => t[0] === item[0]) === index)}
                    onChange={(v) => updateSpeaker(speaker.id, { voice: v })}
                  />
                </div>
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
