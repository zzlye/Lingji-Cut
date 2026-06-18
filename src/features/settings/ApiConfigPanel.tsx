// src/features/settings/ApiConfigPanel.tsx
// 文本 API 配置面板 - 管理模型渠道、模型获取、生成参数和自动化参数（shadcn 重做）

import { useEffect, useMemo, useRef, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { profileApi } from '@/lib/api'
import { saveAutomationPreferences } from '@/lib/automationPreferences'
import {
  createTextPromptPreset,
  DEFAULT_TEXT_SYSTEM_PROMPT,
  deleteTextPromptPreset,
  loadActiveTextPromptPresetId,
  loadTextPromptPresets,
  migrateTextPromptPresetsFromProfiles,
  setActiveTextPromptPresetId,
  upsertTextPromptPreset,
} from '@/lib/textPromptPresets'
import type { ApiProfile, TextApiSettings, TextModelOption, TextPromptPreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { TextField, SecretField, SelectField, NumberField, SliderField, SegmentedField, SwitchField, TextareaField, type FieldOption } from '@/components/fields'

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
    response_format: 'text', stream: false,
  }
}

/** 创建默认表单 */
function createProfileForm() {
  const provider = TEXT_PROVIDERS[0]
  return { name: 'OpenAI 文本', provider_type: provider.id, base_url: provider.baseUrl, api_key: '', model: provider.model, custom_model: '' }
}

/** 创建提示词编辑表单 */
function createPromptForm(preset?: TextPromptPreset) {
  return {
    id: preset?.id || 'new',
    name: preset?.name || '短视频字幕提示词',
    prompt: preset?.prompt || DEFAULT_TEXT_SYSTEM_PROMPT,
    description: preset?.description || '',
  }
}

/** 读取 API 参数时忽略旧版混入的 system_prompt */
function parseProfileSettings(extraParams?: string | null): TextApiSettings {
  if (!extraParams) return createDefaultSettings()
  try {
    const parsed = JSON.parse(extraParams)
    const { system_prompt: _legacyPrompt, ...settings } = parsed && typeof parsed === 'object' ? parsed : {}
    return { ...createDefaultSettings(), ...settings }
  } catch {
    return createDefaultSettings()
  }
}

/** 保存 API 参数时不再把提示词写入 API 渠道配置 */
function serializeProfileSettings(settings: TextApiSettings): string {
  const { system_prompt: _legacyPrompt, ...payload } = settings
  return JSON.stringify(payload)
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
  const [showApiKey, setShowApiKey] = useState(false)
  const [promptPresets, setPromptPresets] = useState<TextPromptPreset[]>(() => loadTextPromptPresets())
  const [activePromptPresetId, setActivePromptPresetState] = useState(() => loadActiveTextPromptPresetId())
  const [promptForm, setPromptForm] = useState(() => {
    const presets = loadTextPromptPresets()
    const activeId = loadActiveTextPromptPresetId()
    return createPromptForm(presets.find((preset) => preset.id === activeId) || presets[0])
  })
  const [isPromptEditorOpen, setIsPromptEditorOpen] = useState(false)
  const { addLog } = useTaskStore()
  const profileRequestRef = useRef(0)

  const selectedProfile = useMemo(() => profiles.find((p) => p.id === selectedProfileId) || null, [profiles, selectedProfileId])
  const provider = TEXT_PROVIDERS.find((item) => item.id === profileForm.provider_type) || TEXT_PROVIDERS[0]
  const activeModel = profileForm.custom_model.trim() || profileForm.model
  const activePromptPreset = useMemo(() => promptPresets.find((preset) => preset.id === activePromptPresetId) || promptPresets[0] || null, [promptPresets, activePromptPresetId])

  const loadSavedApiKey = async (profileId: number) => {
    const result = await profileApi.getTextSecret(profileId)
    return result.api_key
  }

  const selectProfile = async (profile: ApiProfile) => {
    // 记录本次切换请求，避免用户快速切换配置时旧请求回写错误的密钥。
    const requestId = ++profileRequestRef.current
    setSelectedProfileId(profile.id)
    saveAutomationPreferences({ text_profile_id: profile.id })
    setShowApiKey(false)
    try {
      const apiKey = await loadSavedApiKey(profile.id)
      if (requestId !== profileRequestRef.current) return
      setProfileForm({ name: profile.name, provider_type: profile.provider_type, base_url: profile.base_url, api_key: apiKey, model: profile.model || '', custom_model: '' })
    } catch (error) {
      if (requestId !== profileRequestRef.current) return
      setProfileForm({ name: profile.name, provider_type: profile.provider_type, base_url: profile.base_url, api_key: '', model: profile.model || '', custom_model: '' })
      addLog('warn', `读取已保存文本 API Key 失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
    setSettings(parseProfileSettings(profile.extra_params))
    setModelOptions(profile.model ? [{ id: profile.model, label: profile.model }] : [])
  }

  const loadProfiles = async (preferredProfileId?: number) => {
    try {
      const data = await profileApi.listText()
      setProfiles(data)
      const migratedPrompts = migrateTextPromptPresetsFromProfiles(data)
      refreshPromptPresets(migratedPrompts)
      const target = preferredProfileId
        ? data.find((item) => item.id === preferredProfileId) || null
        : selectedProfileId === null
          ? data[0] || null
          : null
      if (target) await selectProfile(target)
    } catch (error) {
      addLog('error', `加载文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => { loadProfiles() }, [])

  const createNewProfile = () => {
    profileRequestRef.current += 1
    setSelectedProfileId(null)
    setProfileForm(createProfileForm())
    setSettings(createDefaultSettings())
    setModelOptions([])
    setShowApiKey(false)
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
      const payload = { name: profileForm.name, provider_type: profileForm.provider_type, base_url: profileForm.base_url, api_key: profileForm.api_key || undefined, model: activeModel, extra_params: serializeProfileSettings(settings) }
      const saved = selectedProfileId ? await profileApi.updateText(selectedProfileId, payload) : await profileApi.createText({ ...payload, api_key: profileForm.api_key })
      saveAutomationPreferences({ text_profile_id: saved.id })
      toast.success(`文本 API 配置 "${saved.name}" 已保存`)
      addLog('info', `文本 API 配置 "${saved.name}" 已保存`)
      await loadProfiles(saved.id)
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

  const refreshPromptPresets = (nextPresets = loadTextPromptPresets()) => {
    setPromptPresets(nextPresets)
    const activeId = loadActiveTextPromptPresetId()
    const normalizedActiveId = nextPresets.some((preset) => preset.id === activeId) ? activeId : nextPresets[0]?.id || ''
    if (normalizedActiveId && normalizedActiveId !== activeId) setActiveTextPromptPresetId(normalizedActiveId)
    setActivePromptPresetState(normalizedActiveId)
    if (promptForm.id !== 'new' && !nextPresets.some((preset) => preset.id === promptForm.id)) {
      setPromptForm(createPromptForm(nextPresets.find((preset) => preset.id === normalizedActiveId) || nextPresets[0]))
    }
  }

  const openPromptEditor = (preset?: TextPromptPreset) => {
    setPromptForm(createPromptForm(preset))
    setIsPromptEditorOpen(true)
  }

  const handleSavePromptPreset = () => {
    const prompt = promptForm.prompt.trim()
    if (!prompt) { addLog('warn', '请填写提示词内容'); return }
    const preset = createTextPromptPreset(promptForm.name, prompt, promptForm.description)
    const payload = promptForm.id === 'new' ? preset : { ...preset, id: promptForm.id }
    const next = upsertTextPromptPreset(payload)
    setPromptForm(createPromptForm(payload))
    refreshPromptPresets(next)
    setIsPromptEditorOpen(false)
    toast.success('提示词预设已保存')
  }

  const handleActivatePromptPreset = (presetId: string) => {
    const preset = promptPresets.find((item) => item.id === presetId)
    if (!preset) { toast.warning('提示词预设不存在'); return }
    setActiveTextPromptPresetId(preset.id)
    setActivePromptPresetState(preset.id)
    setPromptForm(createPromptForm(preset))
    toast.success(`已启用提示词：${preset.name}`)
  }

  const handleDeletePromptPreset = (presetId: string) => {
    const preset = promptPresets.find((item) => item.id === presetId)
    if (!preset) return
    if (promptPresets.length <= 1) {
      toast.warning('至少保留一个提示词预设')
      return
    }
    const next = deleteTextPromptPreset(presetId)
    refreshPromptPresets(next)
    const fallback = next.find((preset) => preset.id === loadActiveTextPromptPresetId()) || next[0]
    setPromptForm(createPromptForm(fallback))
    if (promptForm.id === presetId) setIsPromptEditorOpen(false)
    toast.success(`已删除提示词：${preset.name}`)
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
            <SecretField
              label="API Key"
              value={profileForm.api_key}
              placeholder={selectedProfile ? '已保存密钥' : '请输入 API Key'}
              description={selectedProfile ? '已保存配置会自动带出密钥，默认隐藏；点击右侧眼睛可查看。' : undefined}
              visible={showApiKey}
              maskWhenHidden={Boolean(selectedProfile)}
              onToggleVisible={() => setShowApiKey((current) => !current)}
              onChange={(v) => setProfileForm({ ...profileForm, api_key: v })}
            />
            <SelectField label="模型" value={profileForm.model} options={modelOpts} placeholder="先获取模型或填写自定义模型" onChange={(v) => setProfileForm({ ...profileForm, model: v, custom_model: '' })} />
            <TextField label="自定义模型" value={profileForm.custom_model} placeholder={profileForm.model || provider.model || '例如 gpt-4.1-mini'} onChange={(v) => setProfileForm({ ...profileForm, custom_model: v })} />
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button onClick={handleSaveProfile} disabled={isSaving}>{isSaving ? '保存中…' : '保存配置'}</Button>
            <Button variant="outline" onClick={handleLoadModels} disabled={isLoadingModels}>{isLoadingModels ? '获取中…' : '获取模型'}</Button>
            <Button variant="outline" onClick={handleTestProfile} disabled={isTesting || !selectedProfileId}>{isTesting ? '测试中…' : '测试连接'}</Button>
            <span className="text-xs text-muted-foreground">当前模型：{activeModel || '未选择'}</span>
          </div>
        </CardContent>
      </Card>

      {/* 独立提示词预设 */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle className="text-sm">提示词预设</CardTitle>
          <Button variant="outline" size="sm" onClick={() => openPromptEditor()}>
            <Plus className="mr-1.5 size-4" />
            添加提示词
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/25 px-4 py-3 text-sm text-muted-foreground">
            共 {promptPresets.length} 个提示词 · 已启用：{activePromptPreset ? 1 : 0}
          </div>

          <div className="space-y-3">
            {promptPresets.map((preset) => {
              const isActivePrompt = preset.id === activePromptPresetId
              return (
                <div
                  key={preset.id}
                  className={cn(
                    'flex items-center gap-4 rounded-xl border bg-card px-4 py-3 transition-colors',
                    isActivePrompt && 'border-primary/50 bg-primary/5',
                  )}
                >
                  <Switch
                    checked={isActivePrompt}
                    onCheckedChange={(checked) => {
                      if (checked) {
                        handleActivatePromptPreset(preset.id)
                      } else {
                        toast.warning('至少需要启用一个提示词预设')
                      }
                    }}
                    aria-label={`启用 ${preset.name}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-medium">{preset.name}</p>
                      {isActivePrompt && <span className="rounded bg-success/10 px-1.5 py-0.5 text-[11px] text-success">启用中</span>}
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{preset.description || '未填写说明'}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => openPromptEditor(preset)}
                      aria-label={`编辑 ${preset.name}`}
                      title="编辑提示词"
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => handleDeletePromptPreset(preset.id)}
                      disabled={promptPresets.length <= 1}
                      aria-label={`删除 ${preset.name}`}
                      title={promptPresets.length <= 1 ? '至少保留一个提示词' : '删除提示词'}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
          <Dialog open={isPromptEditorOpen} onOpenChange={setIsPromptEditorOpen}>
            <DialogContent className="sm:max-w-4xl">
              <DialogHeader>
                <DialogTitle>{promptForm.id === 'new' ? '添加提示词' : '编辑提示词'}</DialogTitle>
                <DialogDescription>提示词会用于字幕翻译、润色和一键完成。</DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <TextField label="名称" value={promptForm.name} onChange={(v) => setPromptForm({ ...promptForm, name: v })} />
                  <TextField label="描述" value={promptForm.description} placeholder="例如：翻译时严格保留术语" onChange={(v) => setPromptForm({ ...promptForm, description: v })} />
                </div>
                <TextareaField label="内容" value={promptForm.prompt} rows={14} onChange={(v) => setPromptForm({ ...promptForm, prompt: v })} />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setIsPromptEditorOpen(false)}>取消</Button>
                <Button onClick={handleSavePromptPreset}>保存</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
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
