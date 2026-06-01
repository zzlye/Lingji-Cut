// src/features/settings/ApiConfigPanel.tsx
// 文本 API 配置面板 - 管理模型渠道、模型获取、生成参数和自动化参数

import { useEffect, useMemo, useState } from 'react'
import { profileApi } from '@/lib/api'
import type { ApiProfile, TextApiSettings, TextModelOption } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/** 文本 API 渠道配置 */
const TEXT_PROVIDERS = [
  {
    id: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4.1-mini',
    description: '官方 OpenAI API，支持 /models 拉取模型列表。',
  },
  {
    id: 'openai_compatible',
    name: 'OpenAI 兼容',
    baseUrl: 'https://api.example.com/v1',
    model: '',
    description: 'New API、One API、硅基流动等兼容渠道，按 OpenAI /models 读取。',
  },
  {
    id: 'gemini',
    name: 'Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta',
    model: 'gemini-2.5-flash',
    description: 'Google Gemini API，使用 models 列表和 generationConfig 参数。',
  },
  {
    id: 'gemini_compatible',
    name: 'Gemini 兼容',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta',
    model: '',
    description: '兼容 Gemini 路径的第三方渠道。',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    baseUrl: 'https://api.anthropic.com/v1',
    model: 'claude-sonnet-4-5',
    description: 'Claude Messages API，模型列表从 /models 读取。',
  },
  {
    id: 'minimax',
    name: 'MiniMax 文本',
    baseUrl: 'https://api.minimax.io/v1',
    model: '',
    description: 'MiniMax 文本渠道，优先按 OpenAI 兼容格式接入。',
  },
  {
    id: 'xiaomi_mimo',
    name: '小米 MiMo 文本',
    baseUrl: 'https://api.xiaomimimo.com/v1',
    model: '',
    description: '小米 MiMo 文本渠道，优先按 OpenAI 兼容格式接入。',
  },
  {
    id: 'custom',
    name: '自定义渠道',
    baseUrl: '',
    model: '',
    description: '用于私有网关或特殊转发服务，可手动填写模型。',
  },
]

/** 文本 API 默认参数 */
function createDefaultSettings(): TextApiSettings {
  return {
    temperature: 0.7,
    top_p: 1,
    top_k: 40,
    max_tokens: 2048,
    concurrency: 2,
    timeout_seconds: 120,
    retry_count: 2,
    retry_interval_ms: 1200,
    rate_limit_rpm: 60,
    subtitle_batch_size: 12,
    subtitle_batch_chars: 2800,
    system_prompt: '你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。',
    response_format: 'text',
    stream: false,
  }
}

/** 创建默认表单 */
function createProfileForm() {
  const provider = TEXT_PROVIDERS[0]
  return {
    name: 'OpenAI 文本',
    provider_type: provider.id,
    base_url: provider.baseUrl,
    api_key: '',
    model: provider.model,
    custom_model: '',
  }
}

/**
 * 文本 API 配置面板
 * 支持保存多个文本模型渠道、远程获取模型、参数配置和自动化执行参数。
 */
export function ApiConfigPanel({ compact = false }: { compact?: boolean }) {
  const [profiles, setProfiles] = useState<ApiProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [profileForm, setProfileForm] = useState(createProfileForm)
  const [settings, setSettings] = useState<TextApiSettings>(() => createDefaultSettings())
  const [modelOptions, setModelOptions] = useState<TextModelOption[]>([])
  const [isSaving, setIsSaving] = useState(false)
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const { addLog } = useTaskStore()

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) || null,
    [profiles, selectedProfileId],
  )
  const provider = TEXT_PROVIDERS.find((item) => item.id === profileForm.provider_type) || TEXT_PROVIDERS[0]
  const activeModel = profileForm.custom_model.trim() || profileForm.model

  /** 加载文本 API 配置列表 */
  const loadProfiles = async () => {
    try {
      const data = await profileApi.listText()
      setProfiles(data)
      if (data.length > 0 && selectedProfileId === null) {
        selectProfile(data[0])
      }
    } catch (error) {
      addLog('error', `加载文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadProfiles()
  }, [])

  /** 选择已有文本配置 */
  const selectProfile = (profile: ApiProfile) => {
    setSelectedProfileId(profile.id)
    setProfileForm({
      name: profile.name,
      provider_type: profile.provider_type,
      base_url: profile.base_url,
      api_key: '',
      model: profile.model || '',
      custom_model: '',
    })

    if (profile.extra_params) {
      try {
        setSettings({ ...createDefaultSettings(), ...JSON.parse(profile.extra_params) })
      } catch {
        setSettings(createDefaultSettings())
      }
    } else {
      setSettings(createDefaultSettings())
    }

    if (profile.model) {
      setModelOptions([{ id: profile.model, label: profile.model }])
    } else {
      setModelOptions([])
    }
  }

  /** 新建文本配置 */
  const createNewProfile = () => {
    setSelectedProfileId(null)
    setProfileForm(createProfileForm())
    setSettings(createDefaultSettings())
    setModelOptions([])
  }

  /** 切换渠道并带出默认 Base URL 和模型 */
  const updateProvider = (providerType: string) => {
    const nextProvider = TEXT_PROVIDERS.find((item) => item.id === providerType) || TEXT_PROVIDERS[0]
    setProfileForm((current) => ({
      ...current,
      name: current.name || nextProvider.name,
      provider_type: nextProvider.id,
      base_url: nextProvider.baseUrl || current.base_url,
      model: nextProvider.model || '',
      custom_model: '',
    }))
    setModelOptions(nextProvider.model ? [{ id: nextProvider.model, label: nextProvider.model }] : [])
  }

  /** 获取远程模型列表 */
  const handleLoadModels = async () => {
    if (!profileForm.base_url.trim()) {
      addLog('warn', '请先填写 Base URL')
      return
    }
    if (!profileForm.api_key.trim() && !selectedProfileId) {
      addLog('warn', '请填写 API Key，或先选择已保存的配置')
      return
    }

    setIsLoadingModels(true)
    try {
      const result = await profileApi.listTextModels({
        provider_type: profileForm.provider_type,
        base_url: profileForm.base_url,
        api_key: profileForm.api_key || undefined,
        profile_id: selectedProfileId,
      })
      setModelOptions(result.models)
      if (!profileForm.model && result.models.length > 0) {
        setProfileForm((current) => ({ ...current, model: result.models[0].id }))
      }
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `获取模型失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsLoadingModels(false)
    }
  }

  /** 保存文本 API 配置 */
  const handleSaveProfile = async () => {
    if (!profileForm.name.trim() || !profileForm.base_url.trim()) {
      addLog('warn', '请填写配置名称和 Base URL')
      return
    }
    if (!activeModel.trim()) {
      addLog('warn', '请选择或填写模型')
      return
    }
    if (!selectedProfileId && !profileForm.api_key.trim()) {
      addLog('warn', '新建文本 API 配置需要填写 API Key')
      return
    }

    setIsSaving(true)
    try {
      const payload = {
        name: profileForm.name,
        provider_type: profileForm.provider_type,
        base_url: profileForm.base_url,
        api_key: profileForm.api_key || undefined,
        model: activeModel,
        extra_params: JSON.stringify(settings),
      }
      const saved = selectedProfileId
        ? await profileApi.updateText(selectedProfileId, payload)
        : await profileApi.createText({ ...payload, api_key: profileForm.api_key })

      addLog('info', `文本 API 配置 "${saved.name}" 已保存`)
      await loadProfiles()
      setSelectedProfileId(saved.id)
      setProfileForm((current) => ({ ...current, api_key: '' }))
    } catch (error) {
      addLog('error', `保存文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  /** 测试文本 API 配置 */
  const handleTestProfile = async () => {
    if (!selectedProfileId) {
      addLog('warn', '请先保存并选择文本 API 配置')
      return
    }

    setIsTesting(true)
    try {
      const result = await profileApi.test('text', selectedProfileId)
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `测试文本 API 失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsTesting(false)
    }
  }

  /** 更新文本 API 参数 */
  const updateSetting = <K extends keyof TextApiSettings>(key: K, value: TextApiSettings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!compact && (
        <div className="border-b border-border px-4 py-3">
          <h3 className="text-sm font-medium">文本 API 设置</h3>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(200px,240px)_minmax(0,1fr)] gap-4 max-lg:grid-cols-1">
          <aside className="rounded-lg border border-border bg-background p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div>
                <h4 className="text-sm font-medium">文本 API</h4>
                <p className="text-xs text-foreground-muted">用于字幕生成、翻译、润色</p>
              </div>
              <button onClick={createNewProfile} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5">
                新建
              </button>
            </div>

            <div className="space-y-2">
              {profiles.length === 0 && (
                <div className="rounded-md border border-dashed border-border p-3 text-xs text-foreground-muted">
                  还没有文本 API。右侧填写渠道、密钥、模型和参数后保存。
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
              <SectionTitle title="渠道、密钥和模型" description={provider.description} />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <TextField label="配置名称" value={profileForm.name} onChange={(value) => setProfileForm({ ...profileForm, name: value })} />
                <SelectField
                  label="渠道"
                  value={profileForm.provider_type}
                  options={TEXT_PROVIDERS.map((item) => [item.id, item.name])}
                  onChange={updateProvider}
                />
                <TextField label="Base URL" value={profileForm.base_url} onChange={(value) => setProfileForm({ ...profileForm, base_url: value })} />
                <PasswordField
                  label={selectedProfile ? 'API Key（留空则保留已保存密钥）' : 'API Key'}
                  value={profileForm.api_key}
                  onChange={(value) => setProfileForm({ ...profileForm, api_key: value })}
                />
              </div>

              <div className="mt-4 grid grid-cols-[minmax(220px,1fr)_minmax(220px,320px)] gap-3 max-xl:grid-cols-1">
                <SelectField
                  label="模型列表"
                  value={profileForm.model}
                  options={modelSelectOptions(modelOptions, profileForm.model)}
                  onChange={(value) => setProfileForm({ ...profileForm, model: value, custom_model: '' })}
                />
                <TextField
                  label="自定义模型"
                  value={profileForm.custom_model}
                  placeholder={profileForm.model || provider.model || '例如 gpt-4.1-mini'}
                  onChange={(value) => setProfileForm({ ...profileForm, custom_model: value })}
                />
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <button onClick={handleSaveProfile} disabled={isSaving} className="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  {isSaving ? '保存中...' : '保存配置'}
                </button>
                <button onClick={handleLoadModels} disabled={isLoadingModels} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50">
                  {isLoadingModels ? '获取中...' : '获取模型'}
                </button>
                <button onClick={handleTestProfile} disabled={isTesting || !selectedProfileId} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50">
                  {isTesting ? '测试中...' : '测试连接'}
                </button>
                <span className="text-xs text-foreground-muted">当前模型：{activeModel || '未选择'}</span>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="生成参数" description="OpenAI、Gemini、Anthropic 会按各自字段映射，未支持的字段会在调用时忽略。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                <NumberField label="Temperature" value={settings.temperature} min={0} max={2} step={0.1} onChange={(value) => updateSetting('temperature', value)} />
                <NumberField label="Top P" value={settings.top_p} min={0} max={1} step={0.05} onChange={(value) => updateSetting('top_p', value)} />
                <NumberField label="Top K（Gemini）" value={settings.top_k} min={1} max={100} step={1} onChange={(value) => updateSetting('top_k', value)} />
                <NumberField label="Max Tokens" value={settings.max_tokens} min={64} max={128000} step={64} onChange={(value) => updateSetting('max_tokens', value)} />
                <SelectField label="响应格式" value={settings.response_format} options={[['text', '文本'], ['json', 'JSON']]} onChange={(value) => updateSetting('response_format', value as TextApiSettings['response_format'])} />
                <ToggleField label="流式输出" checked={settings.stream} onChange={(value) => updateSetting('stream', value)} />
              </div>
              <label className="mt-3 block">
                <span className="mb-1 block text-xs text-foreground-muted">系统提示词</span>
                <textarea
                  value={settings.system_prompt}
                  onChange={(event) => updateSetting('system_prompt', event.target.value)}
                  rows={3}
                  className="w-full resize-none rounded-md border border-border bg-background-elevated px-3 py-2 text-sm outline-none focus:border-primary"
                />
              </label>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="自动化和稳定性" description="一键流程会用这些参数控制字幕生成、翻译、润色等批处理请求。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                <NumberField label="并发数" value={settings.concurrency} min={1} max={16} step={1} onChange={(value) => updateSetting('concurrency', Math.round(value))} />
                <NumberField label="超时（秒）" value={settings.timeout_seconds} min={10} max={600} step={5} onChange={(value) => updateSetting('timeout_seconds', Math.round(value))} />
                <NumberField label="失败重试" value={settings.retry_count} min={0} max={10} step={1} onChange={(value) => updateSetting('retry_count', Math.round(value))} />
                <NumberField label="重试间隔（毫秒）" value={settings.retry_interval_ms} min={100} max={30000} step={100} onChange={(value) => updateSetting('retry_interval_ms', Math.round(value))} />
                <NumberField label="限速 RPM" value={settings.rate_limit_rpm} min={0} max={10000} step={1} onChange={(value) => updateSetting('rate_limit_rpm', Math.round(value))} />
                <NumberField label="字幕批量条数" value={settings.subtitle_batch_size} min={1} max={60} step={1} onChange={(value) => updateSetting('subtitle_batch_size', Math.round(value))} />
                <NumberField label="批量字符上限" value={settings.subtitle_batch_chars} min={200} max={12000} step={100} onChange={(value) => updateSetting('subtitle_batch_chars', Math.round(value))} />
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  )
}

/** 渠道展示名称 */
function providerLabel(providerType: string) {
  return TEXT_PROVIDERS.find((provider) => provider.id === providerType)?.name || providerType
}

/** 模型下拉选项，保证当前模型即使不在远程列表里也能显示 */
function modelSelectOptions(models: TextModelOption[], currentModel: string) {
  const options = models.map((model) => [model.id, model.label === model.id ? model.id : `${model.label} · ${model.id}`])
  if (currentModel && !options.some(([value]) => value === currentModel)) {
    options.unshift([currentModel, currentModel])
  }
  if (options.length === 0) {
    options.push(['', '请先获取模型或填写自定义模型'])
  }
  return options
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

/** 数字输入 */
function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
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
          <option key={`${optionValue}-${labelText}`} value={optionValue}>{labelText}</option>
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
