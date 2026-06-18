// src/features/settings/TextPromptPresetPanel.tsx
// 提示词预设设置面板 - 独立管理字幕翻译、润色和一键完成使用的提示词

import { useEffect, useMemo, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { profileApi } from '@/lib/api'
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
import type { TextPromptPreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { TextField, TextareaField } from '@/components/fields'
import { cn } from '@/lib/utils'

/** 创建提示词编辑表单 */
function createPromptForm(preset?: TextPromptPreset) {
  return {
    id: preset?.id || 'new',
    name: preset?.name || '短视频字幕提示词',
    prompt: preset?.prompt || DEFAULT_TEXT_SYSTEM_PROMPT,
    description: preset?.description || '',
  }
}

/**
 * 提示词预设设置面板
 */
export function TextPromptPresetPanel() {
  const [promptPresets, setPromptPresets] = useState<TextPromptPreset[]>(() => loadTextPromptPresets())
  const [activePromptPresetId, setActivePromptPresetState] = useState(() => loadActiveTextPromptPresetId())
  const [promptForm, setPromptForm] = useState(() => {
    const presets = loadTextPromptPresets()
    const activeId = loadActiveTextPromptPresetId()
    return createPromptForm(presets.find((preset) => preset.id === activeId) || presets[0])
  })
  const [isPromptEditorOpen, setIsPromptEditorOpen] = useState(false)
  const { addLog } = useTaskStore()

  const activePromptPreset = useMemo(
    () => promptPresets.find((preset) => preset.id === activePromptPresetId) || promptPresets[0] || null,
    [promptPresets, activePromptPresetId],
  )

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

  useEffect(() => {
    let isMounted = true

    const migrateLegacyPrompts = async () => {
      try {
        const profiles = await profileApi.listText()
        if (!isMounted) return
        refreshPromptPresets(migrateTextPromptPresetsFromProfiles(profiles))
      } catch (error) {
        if (!isMounted) return
        addLog('warn', `迁移旧提示词预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
      }
    }

    migrateLegacyPrompts()
    return () => { isMounted = false }
  }, [])

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
    const fallback = next.find((item) => item.id === loadActiveTextPromptPresetId()) || next[0]
    setPromptForm(createPromptForm(fallback))
    if (promptForm.id === presetId) setIsPromptEditorOpen(false)
    toast.success(`已删除提示词：${preset.name}`)
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">提示词预设</h2>
        <p className="text-sm text-muted-foreground">用于字幕翻译、润色和一键完成；独立于文本 API 渠道、Key 和模型配置。</p>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle className="text-sm">预设列表</CardTitle>
          <Button variant="outline" size="sm" onClick={() => openPromptEditor()}>
            <Plus className="mr-1.5 size-4" />
            添加提示词
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-muted/25 px-4 py-3 text-sm text-muted-foreground">
            共 {promptPresets.length} 个提示词 · 当前启用：{activePromptPreset?.name || '无'}
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
        </CardContent>
      </Card>

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
    </div>
  )
}
