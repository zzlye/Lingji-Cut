// src/features/settings/ApiConfigPanel.tsx
// 文本 API 配置面板 - 管理模型渠道、模型获取、生成参数和自动化参数（shadcn 重做）

import { useEffect, useMemo, useState } from 'react'
import { profileApi } from '@/lib/api'
import { saveAutomationPreferences } from '@/lib/automationPreferences'
import type { ApiProfile, TextApiSettings, TextModelOption } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { TextField, SelectField, NumberField, SliderField, SegmentedField, SwitchField, TextareaField, type FieldOption } from '@/components/fields'

/** 文本 API 渠道配置 */
const TEXT_PROVIDERS = [
  { id: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4.1-mini', description: '官方 OpenAI API，支持 /models 拉取模型列表。' },
  { id: 'openai_compatible', name: 'OpenAI 兼容', baseUrl: 'https://api.example.com/v1', model: '', description: 'New API、One API、硅基流动等兼容渠道，按 OpenAI /models 读取。' },
  { id: 'gemini', name: 'Gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.5-flash', description: 'Google Gemini API，使用 models 列表和 generationConfig 参数。' },
  { id: 'gemini_compatible', name: 'Gemini 兼容', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: '', description: '兼容 Gemini 路径的第三方渠道。' },
  { id: 'anthropic', name: 'Anthropic', baseUrl: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-5', description: 'Claude Messages API，模型列表从 /models 读取。' },
  { id: 'minimax', name: 'MiniMax 文本', baseUrl: 'https://api.minimax.io/v1', model: '', description: 'MiniMax 文本渠道，优先按 OpenAI 兼容格式接入。' },
  { id: 'xiaomi_mimo', name: '小米 MiMo 文本', baseUrl: 'https://api.xiaomimimo.com/v1', model: '', description: '小米 MiMo 文本渠道，优先按 OpenAI 兼容格式接入。' },
  { id: 'custom', name: '自定义渠道', baseUrl: '', model: '', description: '用于私有网关或特殊转发服务，可手动填写模型。' },
]

/** 文本 API 默认参数 */
function createDefaultSettings(): TextApiSettings {
  return {
    temperature: 0.7, top_p: 1, top_k: 40, max_tokens: 2048, concurrency: 2, timeout_seconds: 120, retry_count: 2,
    retry_interval_ms: 1200, rate_limit_rpm: 60, subtitle_batch_size: 12, subtitle_batch_chars: 2800,
    system_prompt: '你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。', response_format: 'text', stream: false,
  }
}

/** 创建默认表单 */
function createProfileForm() {
  const provider = TEXT_PROVIDERS[0]
  return { name: 'OpenAI 文本', provider_type: provider.id, base_url: provider.baseUrl, api_key: '', model: provider.model, custom_model: '' }
}

/**
 * 文本 API 配置面板
 */
export function ApiConfigPanel({ compact = false }: { compact?: boolean }) {
  void compact
  const [profiles, setProfiles] = useState<ApiProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [profileForm, setProfileForm] = useState(createProfileForm)
  const [settings, setSettings] = useState<TextApiSettings>(() => createDefaultSettings())
  const [modelOptions, setModelOptions] = useState<TextModelOption[]>([])
  const [isSaving, setIsSaving] = useState(false)
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const { addLog } = useTaskStore()

  const selectedProfile = useMemo(() => profiles.find((p) => p.id === selectedProfileId) || null, [profiles, selectedProfileId])
  const provider = TEXT_PROVIDERS.find((item) => item.id === profileForm.provider_type) || TEXT_PROVIDERS[0]
  const activeModel = profileForm.custom_model.trim() || profileForm.model

  const loadProfiles = async () => {
    try {
      const data = await profileApi.listText()
      setProfiles(data)
      if (data.length > 0 && selectedProfileId === null) selectProfile(data[0])
    } catch (error) {
      addLog('error', `加载文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => { loadProfiles() }, [])

  const selectProfile = (profile: ApiProfile) => {
    setSelectedProfileId(profile.id)
    saveAutomationPreferences({ text_profile_id: profile.id })
    setProfileForm({ name: profile.name, provider_type: profile.provider_type, base_url: profile.base_url, api_key: '', model: profile.model || '', custom_model: '' })
    if (profile.extra_params) {
      try { setSettings({ ...createDefaultSettings(), ...JSON.parse(profile.extra_params) }) } catch { setSettings(createDefaultSettings()) }
    } else { setSettings(createDefaultSettings()) }
    setModelOptions(profile.model ? [{ id: profile.model, label: profile.model }] : [])
  }

  const createNewProfile = () => {
    setSelectedProfileId(null)
    setProfileForm(createProfileForm())
    setSettings(createDefaultSettings())
    setModelOptions([])
  }

  const updateProvider = (providerType: string) => {
    const next = TEXT_PROVIDERS.find((item) => item.id === providerType) || TEXT_PROVIDERS[0]
    setProfileForm((current) => ({ ...current, name: current.name || next.name, provider_type: next.id, base_url: next.baseUrl || current.base_url, model: next.model || '', custom_model: '' }))
    setModelOptions(next.model ? [{ id: next.model, label: next.model }] : [])
  }

  const handleLoadModels = async () => {
    if (!profileForm.base_url.trim()) { addLog('warn', '请先填写 Base URL'); return }
    if (!profileForm.api_key.trim() && !selectedProfileId) { addLog('warn', '请填写 API Key，或先选择已保存的配置'); return }
    setIsLoadingModels(true)
    try {
      const result = await profileApi.listTextModels({ provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, profile_id: selectedProfileId })
      setModelOptions(result.models)
      if (!profileForm.model && result.models.length > 0) setProfileForm((current) => ({ ...current, model: result.models[0].id }))
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `获取模型失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setIsLoadingModels(false) }
  }

  const handleSaveProfile = async () => {
    if (!profileForm.name.trim() || !profileForm.base_url.trim()) { addLog('warn', '请填写配置名称和 Base URL'); return }
    if (!activeModel.trim()) { addLog('warn', '请选择或填写模型'); return }
    if (!selectedProfileId && !profileForm.api_key.trim()) { addLog('warn', '新建文本 API 配置需要填写 API Key'); return }
    setIsSaving(true)
    try {
      const payload = { name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: JSON.stringify(settings) }
      const saved = selectedProfileId ? await profileApi.updateText(selectedProfileId, payload) : await profileApi.createText({ ...payload, api_key: profileForm.api_key })
      saveAutomationPreferences({ text_profile_id: saved.id })
      toast.success(`文本 API 配置 "${saved.name}" 已保存`)
      addLog('info', `文本 API 配置 "${saved.name}" 已保存`)
      await loadProfiles()
      setSelectedProfileId(saved.id)
      setProfileForm((current) => ({ ...current, api_key: '' }))
    } catch (error) {
      addLog('error', `保存文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setIsSaving(false) }
  }

  const handleTestProfile = async () => {
    if (!selectedProfileId) { toast.warning('请先保存并选择文本 API 配置后再测试'); return }
    setIsTesting(true)
    try {
      const result = await profileApi.test('text', selectedProfileId)
      toast.success(result.message || '文本 API 连接正常，可以使用')
      addLog('info', result.message)
    } catch (error) {
      const message = `测试文本 API 失败: ${error instanceof Error ? error.message : '未知错误'}`
      toast.error(message); addLog('error', message)
    } finally { setIsTesting(false) }
  }

  const updateSetting = <K extends keyof TextApiSettings>(key: K, value: TextApiSettings[K]) => setSettings((current) => ({ ...current, [key]: value }))

  const modelOpts: FieldOption[] = (() => {
    const opts = modelOptions.map((m) => [m.id, m.label === m.id ? m.id : `${m.label} · ${m.id}`] as FieldOption)
    if (profileForm.model && !opts.some(([v]) => v === profileForm.model)) opts.unshift([profileForm.model, profileForm.model])
    return opts
  })()

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">文本 API</h2>
        <p className="text-sm text-muted-foreground">用于字幕生成、翻译、润色。配好渠道与模型后点「测试连接」确认是否可用。</p>
      </div>

      {/* 已保存渠道 */}
      <div className="flex flex-wrap items-center gap-2">
        {profiles.map((profile) => (
          <button key={profile.id} onClick={() => selectProfile(profile)} className={cn('rounded-lg border px-3 py-2 text-left text-sm transition-colors', selectedProfileId === profile.id ? 'border-primary bg-primary/10' : 'bg-card hover:border-primary/50')}>
            <span className="block font-medium">{profile.name}</span>
            <span className="block text-xs text-muted-foreground">{providerLabel(profile.provider_type)}</span>
          </button>
        ))}
        <Button variant="outline" size="sm" onClick={createNewProfile}>+ 新建渠道</Button>
      </div>

      {/* 渠道与模型 */}
      <Card>
        <CardHeader><CardTitle className="text-sm">渠道与模型</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="配置名称" value={profileForm.name} onChange={(v) => setProfileForm({ ...profileForm, name: v })} />
            <SelectField label="渠道" value={profileForm.provider_type} options={TEXT_PROVIDERS.map((p) => [p.id, p.name] as FieldOption)} onChange={updateProvider} description={provider.description} />
            <TextField label="Base URL" value={profileForm.base_url} onChange={(v) => setProfileForm({ ...profileForm, base_url: v })} />
            <TextField label={selectedProfile ? 'API Key（留空保留已存密钥）' : 'API Key'} type="password" value={profileForm.api_key} onChange={(v) => setProfileForm({ ...profileForm, api_key: v })} />
            <SelectField label="模型" value={profileForm.model} options={modelOpts} placeholder="先获取模型或填写自定义模型" onChange={(v) => setProfileForm({ ...profileForm, model: v, custom_model: '' })} />
            <TextField label="自定义模型" value={profileForm.custom_model} placeholder={profileForm.model || provider.model || '例如 gpt-4.1-mini'} onChange={(v) => setProfileForm({ ...profileForm, custom_model: v })} />
          </div>
          <TextareaField label="系统提示词" value={settings.system_prompt} rows={3} onChange={(v) => updateSetting('system_prompt', v)} />
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button onClick={handleSaveProfile} disabled={isSaving}>{isSaving ? '保存中…' : '保存配置'}</Button>
            <Button variant="outline" onClick={handleLoadModels} disabled={isLoadingModels}>{isLoadingModels ? '获取中…' : '获取模型'}</Button>
            <Button variant="outline" onClick={handleTestProfile} disabled={isTesting || !selectedProfileId}>{isTesting ? '测试中…' : '测试连接'}</Button>
            <span className="text-xs text-muted-foreground">当前模型：{activeModel || '未选择'}</span>
          </div>
        </CardContent>
      </Card>

      {/* 高级 */}
      <Accordion type="multiple" className="space-y-2">
        <AccordionItem value="gen" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">生成参数</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SliderField label="Temperature（随机性）" value={settings.temperature} min={0} max={2} step={0.1} format={(v) => v.toFixed(1)} onChange={(v) => updateSetting('temperature', v)} />
            <SliderField label="Top P" value={settings.top_p} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => updateSetting('top_p', v)} />
            <SliderField label="Top K（Gemini）" value={settings.top_k} min={1} max={100} step={1} onChange={(v) => updateSetting('top_k', v)} />
            <NumberField label="Max Tokens" value={settings.max_tokens} min={64} max={128000} step={64} onChange={(v) => updateSetting('max_tokens', Math.round(v))} />
            <SegmentedField label="响应格式" value={settings.response_format} options={[['text', '文本'], ['json', 'JSON']]} onChange={(v) => updateSetting('response_format', v as TextApiSettings['response_format'])} />
            <SwitchField label="流式输出" checked={settings.stream} onChange={(v) => updateSetting('stream', v)} />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="batch" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">批处理与稳定性（字幕逐条处理）</AccordionTrigger>
          <AccordionContent className="grid gap-3 pb-3 sm:grid-cols-2">
            <NumberField label="并发数" value={settings.concurrency} min={1} max={16} step={1} onChange={(v) => updateSetting('concurrency', Math.round(v))} />
            <NumberField label="超时" value={settings.timeout_seconds} min={10} max={600} step={5} suffix="秒" onChange={(v) => updateSetting('timeout_seconds', Math.round(v))} />
            <NumberField label="失败重试" value={settings.retry_count} min={0} max={10} step={1} suffix="次" onChange={(v) => updateSetting('retry_count', Math.round(v))} />
            <NumberField label="重试间隔" value={settings.retry_interval_ms} min={100} max={30000} step={100} suffix="ms" onChange={(v) => updateSetting('retry_interval_ms', Math.round(v))} />
            <NumberField label="限速 RPM" value={settings.rate_limit_rpm} min={0} max={10000} step={1} suffix="/分" onChange={(v) => updateSetting('rate_limit_rpm', Math.round(v))} />
            <NumberField label="字幕批量条数" value={settings.subtitle_batch_size} min={1} max={60} step={1} onChange={(v) => updateSetting('subtitle_batch_size', Math.round(v))} />
            <NumberField label="批量字符上限" value={settings.subtitle_batch_chars} min={200} max={12000} step={100} onChange={(v) => updateSetting('subtitle_batch_chars', Math.round(v))} />
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

/** 渠道展示名称 */
function providerLabel(providerType: string) {
  return TEXT_PROVIDERS.find((provider) => provider.id === providerType)?.name || providerType
}
