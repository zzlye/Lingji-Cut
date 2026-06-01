// src/features/voice/VoiceConfigPanel.tsx
// 配音配置面板 - 管理配音 API、音色、试听和生成参数

import { useEffect, useMemo, useState } from 'react'
import { profileApi, voiceApi } from '@/lib/api'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { ApiProfile, VoiceGenerateSettings, VoiceOption } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/** 配音渠道配置 */
const VOICE_PROVIDERS = [
  { id: 'openai_tts', name: 'OpenAI TTS', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini-tts' },
  { id: 'gemini_tts', name: 'Gemini TTS', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.5-flash-preview-tts' },
  { id: 'minimax_tts', name: 'MiniMax T2A', baseUrl: 'https://api.minimax.io/v1', model: 'speech-2.8-hd' },
  { id: 'xiaomi_mimo_tts', name: '小米 MiMo TTS', baseUrl: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2-tts' },
  { id: 'custom_tts', name: '自定义 OpenAI 兼容', baseUrl: '', model: '' },
]

/** 默认试听文本 */
const DEFAULT_PREVIEW_TEXT = '这是一段配音试听，用来确认音色、语速、音量和情绪是否适合当前视频。'

/** 配音参数默认值 */
function createDefaultSettings(): VoiceGenerateSettings {
  return {
    speed: 1,
    volume: 1,
    pitch: 0,
    format: 'mp3',
    sample_rate: 32000,
    bitrate: 128000,
    channel: 1,
    emotion: '',
    style_prompt: '',
    language_boost: 'auto',
    intensity: 0,
    timbre: 0,
    voice_pitch: 0,
    sound_effects: '',
  }
}

/** 配音 API 表单 */
function createProfileForm() {
  const provider = VOICE_PROVIDERS[0]
  return {
    name: 'OpenAI 配音',
    provider_type: provider.id,
    base_url: provider.baseUrl,
    api_key: '',
    model: provider.model,
  }
}

/**
 * 配音配置面板
 * 配置配音 API、音色、语速、音量、音调、输出格式并支持试听。
 */
export function VoiceConfigPanel({ compact = false }: { compact?: boolean }) {
  const [profiles, setProfiles] = useState<ApiProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [profileForm, setProfileForm] = useState(createProfileForm)
  const [settings, setSettings] = useState<VoiceGenerateSettings>(() => createDefaultSettings())
  const [voice, setVoice] = useState('alloy')
  const [customVoice, setCustomVoice] = useState('')
  const [automationOptions, setAutomationOptions] = useState(() => loadAutomationPreferences())
  const [previewText, setPreviewText] = useState(DEFAULT_PREVIEW_TEXT)
  const [audioUrl, setAudioUrl] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isLoadingVoices, setIsLoadingVoices] = useState(false)
  const { addLog } = useTaskStore()

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) || null,
    [profiles, selectedProfileId],
  )

  const activeProvider = selectedProfile?.provider_type || profileForm.provider_type
  const activeProviderMeta = VOICE_PROVIDERS.find((provider) => provider.id === activeProvider) || VOICE_PROVIDERS[0]
  const selectedVoice = customVoice.trim() || voice
  const supportsMiniMaxAdvanced = activeProvider === 'minimax_tts'
  const supportsStylePrompt = activeProvider === 'openai_tts' || activeProvider === 'gemini_tts' || activeProvider === 'xiaomi_mimo_tts' || activeProvider === 'custom_tts'

  /** 加载配音配置列表 */
  const loadProfiles = async () => {
    try {
      const data = await profileApi.listVoice()
      setProfiles(data)
      if (data.length > 0 && selectedProfileId === null) {
        selectProfile(data[0])
      }
    } catch (error) {
      addLog('error', `加载配音配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadProfiles()
  }, [])

  useEffect(() => {
    loadVoices(activeProvider)
  }, [activeProvider])

  /** 选择已有配音 API 配置 */
  const selectProfile = (profile: ApiProfile) => {
    setSelectedProfileId(profile.id)
    setAutomationOptions(saveAutomationPreferences({ voice_profile_id: profile.id, enable_voice: true }))
    setProfileForm({
      name: profile.name,
      provider_type: profile.provider_type,
      base_url: profile.base_url,
      api_key: '',
      model: profile.model || '',
    })

    if (profile.extra_params) {
      try {
        const parsed = JSON.parse(profile.extra_params)
        setSettings({ ...createDefaultSettings(), ...parsed })
        if (parsed.voice) setVoice(parsed.voice)
      } catch {
        setSettings(createDefaultSettings())
      }
    }
  }

  /** 新建配音 API 配置 */
  const createNewProfile = () => {
    setSelectedProfileId(null)
    setProfileForm(createProfileForm())
    setSettings(createDefaultSettings())
    setVoice('alloy')
    setCustomVoice('')
  }

  /** 切换渠道并带出默认地址和模型 */
  const updateProvider = (providerType: string) => {
    const provider = VOICE_PROVIDERS.find((item) => item.id === providerType) || VOICE_PROVIDERS[0]
    setProfileForm((current) => ({
      ...current,
      provider_type: provider.id,
      base_url: provider.baseUrl || current.base_url,
      model: provider.model || current.model,
      name: current.name || provider.name,
    }))
  }

  /** 获取音色目录 */
  const loadVoices = async (providerType: string) => {
    setIsLoadingVoices(true)
    try {
      const result = await voiceApi.voices(providerType)
      setVoices(result.voices)
      if (result.voices.length > 0) {
        setVoice(result.voices[0].id)
      }
    } catch (error) {
      addLog('error', `获取音色失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsLoadingVoices(false)
    }
  }

  /** 保存配音 API 配置 */
  const handleSaveProfile = async () => {
    if (!profileForm.name.trim() || !profileForm.base_url.trim()) {
      addLog('warn', '请填写配音配置名称和 Base URL')
      return
    }
    if (!selectedProfileId && !profileForm.api_key.trim()) {
      addLog('warn', '新建配音配置需要填写 API Key')
      return
    }

    setIsSaving(true)
    try {
      const payload = {
        ...profileForm,
        model: profileForm.model || selectedVoice,
        extra_params: JSON.stringify({ ...settings, voice: selectedVoice }),
      }
      const saved = selectedProfileId
        ? await profileApi.updateVoice(selectedProfileId, payload)
        : await profileApi.createVoice(payload)

      setAutomationOptions(saveAutomationPreferences({ voice_profile_id: saved.id, enable_voice: true }))
      addLog('info', `配音配置 "${saved.name}" 已保存`)
      await loadProfiles()
      setSelectedProfileId(saved.id)
    } catch (error) {
      addLog('error', `保存配音配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  /** 测试连接 */
  const handleTestProfile = async () => {
    if (!selectedProfileId) {
      addLog('warn', '请先保存配音 API 配置')
      return
    }

    try {
      const result = await profileApi.test('voice', selectedProfileId)
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `测试配音 API 失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 试听配音 */
  const handlePreview = async () => {
    if (!selectedProfileId) {
      addLog('warn', '请先保存并选择配音 API 配置')
      return
    }
    if (!previewText.trim()) {
      addLog('warn', '请输入试听文本')
      return
    }

    setIsGenerating(true)
    setAudioUrl('')
    try {
      const result = await voiceApi.generate({
        text: previewText,
        profile_id: selectedProfileId,
        voice: selectedVoice,
        model: profileForm.model,
        settings,
      })
      setAudioUrl(`http://127.0.0.1:8765${result.audio_url}`)
      addLog('info', `试听音频已生成: ${result.output_path}`)
    } catch (error) {
      addLog('error', `试听失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsGenerating(false)
    }
  }

  /** 更新配音参数 */
  const updateSetting = <K extends keyof VoiceGenerateSettings>(key: K, value: VoiceGenerateSettings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!compact && (
        <div className="border-b border-border px-4 py-3">
          <h3 className="text-sm font-medium">配音配置</h3>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(190px,230px)_minmax(0,1fr)] gap-4 max-lg:grid-cols-1">
          <aside className="rounded-lg border border-border bg-background p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div>
                <h4 className="text-sm font-medium">配音 API</h4>
                <p className="text-xs text-foreground-muted">配音渠道在这里管理</p>
              </div>
              <button onClick={createNewProfile} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5">
                新建
              </button>
            </div>

            <div className="space-y-2">
              {profiles.length === 0 && (
                <div className="rounded-md border border-dashed border-border p-3 text-xs text-foreground-muted">
                  还没有配音 API，右侧保存后可试听。
                </div>
              )}
              {profiles.map((profile) => (
                <button
                  key={profile.id}
                  onClick={() => selectProfile(profile)}
                  className={`w-full rounded-md border p-3 text-left transition-colors ${
                    selectedProfileId === profile.id
                      ? 'border-primary bg-primary/10'
                      : 'border-border bg-background-elevated hover:border-border-bright'
                  }`}
                >
                  <div className="truncate text-sm font-medium">{profile.name}</div>
                  <div className="mt-1 text-xs text-foreground-muted">{providerLabel(profile.provider_type)}</div>
                  {profile.model && <div className="mt-1 truncate text-[10px] text-foreground-muted">{profile.model}</div>}
                </button>
              ))}
            </div>
          </aside>

          <main className="min-w-0 space-y-4">
            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="渠道和密钥" description="保存 API 后可测试连接、获取音色并生成试听。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <TextField label="配置名称" value={profileForm.name} onChange={(value) => setProfileForm({ ...profileForm, name: value })} />
                <SelectField
                  label="渠道"
                  value={profileForm.provider_type}
                  options={VOICE_PROVIDERS.map((provider) => [provider.id, provider.name])}
                  onChange={updateProvider}
                />
                <TextField label="Base URL" value={profileForm.base_url} onChange={(value) => setProfileForm({ ...profileForm, base_url: value })} />
                <TextField label="模型" value={profileForm.model} onChange={(value) => setProfileForm({ ...profileForm, model: value })} />
                <PasswordField
                  label={selectedProfileId ? 'API Key（留空则保留已保存密钥）' : 'API Key'}
                  value={profileForm.api_key}
                  onChange={(value) => setProfileForm({ ...profileForm, api_key: value })}
                />
              </div>
              <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-3">
                <button onClick={handleSaveProfile} disabled={isSaving} className="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  {isSaving ? '保存中...' : '保存 API 配置'}
                </button>
                <button onClick={handleTestProfile} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5">
                  测试连接
                </button>
                <button onClick={() => loadVoices(activeProvider)} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5">
                  {isLoadingVoices ? '获取中...' : '获取音色'}
                </button>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="音色和声音" description={`${activeProviderMeta.name} 当前支持的音色目录，可输入自定义 voice id。`} />
              <div className="mt-4 grid grid-cols-[minmax(240px,1fr)_minmax(220px,300px)] gap-3 max-xl:grid-cols-1">
                <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2">
                  {voices.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => {
                        setVoice(item.id)
                        setCustomVoice('')
                      }}
                      className={`rounded-md border p-3 text-left transition-colors ${
                        selectedVoice === item.id ? 'border-primary bg-primary/10' : 'border-border bg-background-elevated hover:border-border-bright'
                      }`}
                    >
                      <div className="truncate text-sm font-medium">{item.name}</div>
                      <div className="mt-1 text-xs text-foreground-muted">{item.language}</div>
                      <div className="mt-1 truncate text-[10px] text-foreground-muted">{item.style}</div>
                    </button>
                  ))}
                </div>
                <div className="space-y-3 rounded-lg border border-border bg-background-elevated p-3">
                  <TextField label="自定义 voice id" value={customVoice} placeholder={voice} onChange={setCustomVoice} />
                  <RangeField label="语速" value={settings.speed} min={0.5} max={2} step={0.05} onChange={(value) => updateSetting('speed', value)} />
                  <RangeField label="音量" value={settings.volume} min={0.1} max={10} step={0.1} onChange={(value) => updateSetting('volume', value)} />
                  <RangeField label="音调" value={settings.pitch} min={-12} max={12} step={1} onChange={(value) => updateSetting('pitch', value)} />
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="一键完成配音策略" description="控制自动化流程是否配音，以及配音和原声如何合成。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                <ToggleField
                  label="启用自动配音"
                  checked={automationOptions.enable_voice}
                  onChange={(value) => setAutomationOptions(saveAutomationPreferences({ enable_voice: value }))}
                />
                <SelectField
                  label="生成方式"
                  value={automationOptions.voice_mode}
                  options={[['segmented', '按字幕分段'], ['full', '整段生成']]}
                  onChange={(value) => setAutomationOptions(saveAutomationPreferences({ voice_mode: value as typeof automationOptions.voice_mode }))}
                />
                <SelectField
                  label="音频合成"
                  value={automationOptions.audio_mode}
                  options={[['mix', '保留原声并混合'], ['replace', '替换原声']]}
                  onChange={(value) => setAutomationOptions(saveAutomationPreferences({ audio_mode: value as typeof automationOptions.audio_mode }))}
                />
                <RangeField
                  label="原声音量"
                  value={automationOptions.original_volume}
                  min={0}
                  max={1}
                  step={0.05}
                  onChange={(value) => setAutomationOptions(saveAutomationPreferences({ original_volume: value }))}
                />
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="输出和高级参数" description="设置音频格式、采样率、码率、情绪和风格提示。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                <SelectField label="格式" value={settings.format} options={[['mp3', 'MP3'], ['wav', 'WAV'], ['flac', 'FLAC'], ['pcm', 'PCM'], ['opus', 'OPUS']]} onChange={(value) => updateSetting('format', value as VoiceGenerateSettings['format'])} />
                <SelectField label="采样率" value={String(settings.sample_rate)} options={['16000', '22050', '24000', '32000', '44100'].map((value) => [value, `${value} Hz`])} onChange={(value) => updateSetting('sample_rate', Number(value))} />
                <SelectField label="码率" value={String(settings.bitrate)} options={['32000', '64000', '128000', '256000'].map((value) => [value, `${Number(value) / 1000} kbps`])} onChange={(value) => updateSetting('bitrate', Number(value))} />
                <SelectField label="声道" value={String(settings.channel)} options={[['1', '单声道'], ['2', '立体声']]} onChange={(value) => updateSetting('channel', Number(value))} />
                {supportsMiniMaxAdvanced && (
                  <>
                    <SelectField label="情绪" value={settings.emotion} options={[['', '自动'], ['happy', '开心'], ['sad', '悲伤'], ['angry', '愤怒'], ['calm', '平静'], ['surprised', '惊讶'], ['whisper', '耳语']]} onChange={(value) => updateSetting('emotion', value)} />
                    <SelectField label="语言增强" value={settings.language_boost} options={[['auto', '自动'], ['Chinese', '中文'], ['English', '英文'], ['Japanese', '日文'], ['Korean', '韩文']]} onChange={(value) => updateSetting('language_boost', value)} />
                    <RangeField label="强度" value={settings.intensity} min={-100} max={100} step={1} onChange={(value) => updateSetting('intensity', value)} />
                    <RangeField label="音色质感" value={settings.timbre} min={-100} max={100} step={1} onChange={(value) => updateSetting('timbre', value)} />
                  </>
                )}
              </div>
              {supportsStylePrompt && (
                <label className="mt-3 block">
                  <span className="mb-1 block text-xs text-foreground-muted">风格提示</span>
                  <textarea
                    value={settings.style_prompt}
                    onChange={(event) => updateSetting('style_prompt', event.target.value)}
                    rows={3}
                    placeholder="例如：用自然口播风格，语气稳定，适合短视频解说。"
                    className="w-full resize-none rounded-md border border-border bg-background-elevated px-3 py-2 text-sm outline-none focus:border-primary"
                  />
                </label>
              )}
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="试听" description="保存 API 配置后生成短音频试听，确认音色和参数。" />
              <textarea
                value={previewText}
                onChange={(event) => setPreviewText(event.target.value)}
                rows={4}
                className="mt-4 w-full resize-none rounded-md border border-border bg-background-elevated px-3 py-2 text-sm outline-none focus:border-primary"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button onClick={handlePreview} disabled={isGenerating || !previewText.trim()} className="h-9 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  {isGenerating ? '生成试听中...' : '生成试听'}
                </button>
                <span className="text-xs text-foreground-muted">当前音色：{selectedVoice}</span>
              </div>
              {audioUrl && (
                <audio className="mt-3 w-full" controls src={audioUrl}>
                  <track kind="captions" />
                </audio>
              )}
            </section>
          </main>
        </div>
      </div>
    </div>
  )
}

/** 渠道展示名称 */
function providerLabel(providerType: string) {
  return VOICE_PROVIDERS.find((provider) => provider.id === providerType)?.name || providerType
}

/** 分组标题 */
function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h4 className="text-sm font-medium">{title}</h4>
      <p className="mt-1 text-xs text-foreground-muted">{description}</p>
    </div>
  )
}

/** 文本输入 */
function TextField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      />
    </label>
  )
}

/** 密码输入 */
function PasswordField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      />
    </label>
  )
}

/** 下拉选择 */
function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[][]; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      >
        {options.map(([optionValue, labelText]) => (
          <option key={optionValue} value={optionValue}>{labelText}</option>
        ))}
      </select>
    </label>
  )
}

/** 开关输入 */
function ToggleField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex h-full min-h-9 items-end">
      <span className="flex h-9 w-full items-center justify-between gap-3 rounded-md border border-border bg-background-elevated px-3 text-sm">
        <span className="text-xs text-foreground-muted">{label}</span>
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="h-4 w-4 accent-primary"
        />
      </span>
    </label>
  )
}

/** 滑杆输入 */
function RangeField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-xs text-foreground-muted">
        <span>{label}</span>
        <span className="font-mono">{value}</span>
      </span>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-primary"
      />
    </label>
  )
}
