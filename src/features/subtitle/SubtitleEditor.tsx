// src/features/subtitle/SubtitleEditor.tsx
// 字幕预设配置面板 - 管理语言、单双行、样式、描边、阴影和实时预览

import { useEffect, useMemo, useState } from 'react'
import { subtitleApi } from '@/lib/api'
import type { SubtitlePreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/** 字幕表单状态 */
type SubtitlePresetForm = Omit<SubtitlePreset, 'id'>

/** 可选语言配置 */
const LANGUAGE_OPTIONS = [
  ['auto', '跟随原视频'],
  ['zh-CN', '中文 简体'],
  ['zh-TW', '中文 繁体'],
  ['en', '英文'],
  ['ja', '日文'],
  ['ko', '韩文'],
  ['es', '西班牙语'],
  ['fr', '法语'],
  ['de', '德语'],
  ['custom', '自定义'],
]

/** 字幕位置配置 */
const POSITION_OPTIONS = [
  ['bottom', '底部'],
  ['center', '居中'],
  ['top', '顶部'],
] as const

/** 字幕预设默认值 */
function createDefaultForm(name = '商业字幕预设'): SubtitlePresetForm {
  return {
    name,
    is_default: false,
    line_mode: 'double',
    language: 'zh-CN',
    font_name: 'Microsoft YaHei',
    font_size: 44,
    font_color: '#FFFFFF',
    secondary_color: '#FDE68A',
    outline_color: '#000000',
    outline_width: 3,
    shadow_enabled: true,
    shadow_color: '#000000',
    shadow_x: 2,
    shadow_y: 3,
    background_alpha: 0,
    position: 'bottom',
    margin_v: 42,
  }
}

/** 将后端预设转换成完整表单，兼容旧数据 */
function presetToForm(preset: SubtitlePreset): SubtitlePresetForm {
  return {
    name: preset.name || '未命名预设',
    is_default: Boolean(preset.is_default),
    line_mode: preset.line_mode || 'double',
    language: preset.language || 'auto',
    font_name: preset.font_name || 'Microsoft YaHei',
    font_size: preset.font_size || 44,
    font_color: preset.font_color || '#FFFFFF',
    secondary_color: preset.secondary_color || '#FDE68A',
    outline_color: preset.outline_color || '#000000',
    outline_width: preset.outline_width ?? 3,
    shadow_enabled: preset.shadow_enabled ?? true,
    shadow_color: preset.shadow_color || '#000000',
    shadow_x: preset.shadow_x ?? 2,
    shadow_y: preset.shadow_y ?? 3,
    background_alpha: preset.background_alpha ?? 0,
    position: preset.position || 'bottom',
    margin_v: preset.margin_v ?? 42,
  }
}

/**
 * 字幕预设配置面板
 * 支持预设管理、语言选择、单双行字幕、描边阴影和实时预览。
 */
export function SubtitleEditor({ compact = false }: { compact?: boolean }) {
  const [presets, setPresets] = useState<SubtitlePreset[]>([])
  const [selectedId, setSelectedId] = useState<number | 'new'>('new')
  const [form, setForm] = useState<SubtitlePresetForm>(() => createDefaultForm())
  const [customLanguage, setCustomLanguage] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isPreviewOpen, setIsPreviewOpen] = useState(true)
  const { addLog } = useTaskStore()

  /** 当前语言是否来自预设列表 */
  const usesCustomLanguage = useMemo(
    () => !LANGUAGE_OPTIONS.some(([value]) => value === form.language),
    [form.language],
  )

  /** 加载预设列表 */
  const loadPresets = async () => {
    try {
      const data = await subtitleApi.listPresets()
      setPresets(data)
      if (data.length > 0 && selectedId === 'new') {
        setSelectedId(data[0].id)
        setForm(presetToForm(data[0]))
      }
    } catch (error) {
      addLog('error', `加载字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadPresets()
  }, [])

  /** 选择已有预设 */
  const handleSelectPreset = (preset: SubtitlePreset) => {
    setSelectedId(preset.id)
    setForm(presetToForm(preset))
    setCustomLanguage('')
  }

  /** 新建预设 */
  const handleNewPreset = () => {
    setSelectedId('new')
    setForm(createDefaultForm(`字幕预设 ${presets.length + 1}`))
    setCustomLanguage('')
  }

  /** 更新表单字段 */
  const updateForm = <K extends keyof SubtitlePresetForm>(key: K, value: SubtitlePresetForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  /** 保存当前预设 */
  const handleSave = async () => {
    const name = form.name.trim()
    if (!name) {
      addLog('warn', '请输入字幕预设名称')
      return
    }

    const language = form.language === 'custom' ? customLanguage.trim() : form.language
    if (!language) {
      addLog('warn', '请输入自定义字幕语言')
      return
    }

    setIsSaving(true)
    try {
      const payload = {
        ...form,
        name,
        language,
        font_size: Number(form.font_size),
        outline_width: Number(form.outline_width),
        shadow_x: Number(form.shadow_x),
        shadow_y: Number(form.shadow_y),
        background_alpha: Number(form.background_alpha),
        margin_v: Number(form.margin_v),
      }
      const saved = selectedId === 'new'
        ? await subtitleApi.createPreset(payload)
        : await subtitleApi.updatePreset(selectedId, payload)
      addLog('info', `字幕预设 "${saved.name}" 已保存`)
      setSelectedId(saved.id)
      setForm(presetToForm(saved))
      await loadPresets()
    } catch (error) {
      addLog('error', `保存字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  /** 删除预设 */
  const handleDelete = async (id: number) => {
    try {
      await subtitleApi.deletePreset(id)
      addLog('info', '字幕预设已删除')
      const nextPresets = presets.filter((preset) => preset.id !== id)
      setPresets(nextPresets)
      if (selectedId === id) {
        if (nextPresets.length > 0) {
          setSelectedId(nextPresets[0].id)
          setForm(presetToForm(nextPresets[0]))
        } else {
          handleNewPreset()
        }
      }
    } catch (error) {
      addLog('error', `删除字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const previewFontSize = Math.max(18, Math.min(42, Number(form.font_size) * 0.58))
  const backgroundAlpha = Math.max(0, Math.min(255, Number(form.background_alpha) || 0))
  const previewBackground = `rgba(0, 0, 0, ${backgroundAlpha / 255})`
  const previewPositionClass = form.position === 'top' ? 'items-start pt-8' : form.position === 'center' ? 'items-center' : 'items-end pb-8'
  const selectedLanguage = usesCustomLanguage ? 'custom' : form.language

  return (
    <div className="h-full min-h-0 flex flex-col">
      {!compact && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-medium">字幕设置</h3>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(168px,210px)_minmax(0,1fr)] max-lg:grid-cols-1 gap-4 items-start">
          <aside className="rounded-lg border border-border bg-background p-3 max-lg:order-1">
            <div className="flex items-center justify-between gap-2 mb-3">
              <div>
                <h4 className="text-sm font-medium">预设</h4>
                <p className="text-xs text-foreground-muted">保存后供一键流程复用</p>
              </div>
              <button
                onClick={handleNewPreset}
                className="h-8 px-3 rounded-md border border-border text-xs hover:bg-white/5"
              >
                新建
              </button>
            </div>

            <div className="space-y-2 max-h-[420px] overflow-auto pr-1">
              {presets.length === 0 && (
                <div className="rounded-md border border-dashed border-border p-3 text-xs text-foreground-muted">
                  还没有字幕预设，右侧配置后保存。
                </div>
              )}
              {presets.map((preset) => (
                <button
                  key={preset.id}
                  onClick={() => handleSelectPreset(preset)}
                  className={`w-full rounded-md border p-3 text-left transition-colors ${
                    selectedId === preset.id
                      ? 'border-primary bg-primary/10'
                      : 'border-border bg-background-elevated hover:border-border-bright'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{preset.name}</span>
                    <span className="shrink-0 text-[10px] text-foreground-muted">
                      {preset.line_mode === 'double' ? '双行' : '单行'}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-xs text-foreground-muted">
                    <span className="h-3 w-3 rounded-sm border border-border" style={{ backgroundColor: preset.font_color }} />
                    <span className="truncate">{preset.language || 'auto'}</span>
                    <span>{preset.font_size}px</span>
                  </div>
                </button>
              ))}
            </div>
          </aside>

          <main className="min-w-0 space-y-4 max-lg:order-2">
            <section className="rounded-lg border border-border bg-background p-4">
              <div className="flex items-center justify-between gap-3">
                <SectionTitle title="实时预览" description="参数变更后立即反映到预览框。" />
                <div className="flex items-center gap-2">
                  <span className="rounded border border-border px-2 py-1 text-[10px] text-foreground-muted">
                    {form.line_mode === 'double' ? '双行' : '单行'}
                  </span>
                  <button
                    onClick={() => setIsPreviewOpen((value) => !value)}
                    className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5"
                  >
                    {isPreviewOpen ? '收起' : '展开'}
                  </button>
                </div>
              </div>

              {isPreviewOpen && (
                <div className="mt-4 grid grid-cols-[minmax(260px,1fr)_minmax(180px,240px)] max-xl:grid-cols-1 gap-3">
                  <div className="aspect-video overflow-hidden rounded-lg border border-border-bright bg-[linear-gradient(135deg,#0f172a_0%,#111827_42%,#7c2d12_100%)]">
                    <div className={`flex h-full px-5 ${previewPositionClass}`}>
                      <div
                        className="max-w-full rounded px-3 py-2 text-center leading-tight"
                        style={{
                          background: previewBackground,
                          color: form.font_color,
                          fontFamily: form.font_name,
                          fontSize: `${previewFontSize}px`,
                          WebkitTextStroke: `${form.outline_width * 0.38}px ${form.outline_color}`,
                          textShadow: form.shadow_enabled
                            ? `${form.shadow_x}px ${form.shadow_y}px 3px ${form.shadow_color}`
                            : 'none',
                        }}
                      >
                        <div className="break-words">这里是视频主字幕预览</div>
                        {form.line_mode === 'double' && (
                          <div className="mt-1 break-words" style={{ color: form.secondary_color }}>
                            Second line subtitle preview
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="grid content-start gap-2 text-xs text-foreground-muted">
                    <PreviewStat label="语言" value={form.language === 'custom' ? customLanguage || '自定义' : form.language} />
                    <PreviewStat label="字体" value={form.font_name} />
                    <PreviewStat label="字号" value={`${form.font_size}px`} />
                    <PreviewStat label="描边" value={`${form.outline_width}px`} />
                  </div>
                </div>
              )}

              <div className="mt-4 flex flex-wrap justify-end gap-2 border-t border-border pt-3">
                {selectedId !== 'new' && (
                  <button
                    onClick={() => handleDelete(selectedId)}
                    className="h-9 rounded-md border border-border px-4 text-sm text-destructive hover:bg-white/5"
                  >
                    删除
                  </button>
                )}
                <button
                  onClick={handleSave}
                  disabled={isSaving}
                  className="h-9 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isSaving ? '保存中...' : '保存预设'}
                </button>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="基础" description="控制字幕来源语言、单双行和屏幕位置。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <TextField label="预设名称" value={form.name} onChange={(value) => updateForm('name', value)} />
                <SelectField
                  label="字幕语言"
                  value={selectedLanguage}
                  options={LANGUAGE_OPTIONS}
                  onChange={(value) => {
                    updateForm('language', value)
                    if (value !== 'custom') setCustomLanguage('')
                  }}
                />
                {selectedLanguage === 'custom' && (
                  <TextField
                    label="自定义语言"
                    value={customLanguage || (usesCustomLanguage ? form.language : '')}
                    placeholder="例如 pt-BR / vi / th"
                    onChange={(value) => {
                      setCustomLanguage(value)
                      updateForm('language', value || 'custom')
                    }}
                  />
                )}
                <SegmentedField
                  label="字幕行数"
                  value={form.line_mode}
                  options={[['single', '单行'], ['double', '双行']]}
                  onChange={(value) => updateForm('line_mode', value as 'single' | 'double')}
                />
                <SegmentedField
                  label="位置"
                  value={form.position}
                  options={POSITION_OPTIONS as unknown as string[][]}
                  onChange={(value) => updateForm('position', value as 'bottom' | 'top' | 'center')}
                />
                <NumberField label="屏幕边距" value={form.margin_v} min={0} max={160} step={2} onChange={(value) => updateForm('margin_v', value)} />
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="文字样式" description="设置字体、字号、主色和双行强调色。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <TextField label="字体" value={form.font_name} onChange={(value) => updateForm('font_name', value)} />
                <NumberField label="文字大小" value={form.font_size} min={18} max={96} step={1} onChange={(value) => updateForm('font_size', value)} />
                <ColorField label="主字幕颜色" value={form.font_color} onChange={(value) => updateForm('font_color', value)} />
                <ColorField label="第二行颜色" value={form.secondary_color} onChange={(value) => updateForm('secondary_color', value)} />
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="描边和阴影" description="字幕必须在浅色和复杂画面上保持可读。" />
              <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <ColorField label="描边颜色" value={form.outline_color} onChange={(value) => updateForm('outline_color', value)} />
                <NumberField label="描边宽度" value={form.outline_width} min={0} max={10} step={1} onChange={(value) => updateForm('outline_width', value)} />
                <ToggleField label="启用阴影" checked={form.shadow_enabled} onChange={(value) => updateForm('shadow_enabled', value)} />
                <ColorField label="阴影颜色" value={form.shadow_color} onChange={(value) => updateForm('shadow_color', value)} disabled={!form.shadow_enabled} />
                <NumberField label="阴影 X" value={form.shadow_x} min={-12} max={12} step={1} onChange={(value) => updateForm('shadow_x', value)} disabled={!form.shadow_enabled} />
                <NumberField label="阴影 Y" value={form.shadow_y} min={-12} max={12} step={1} onChange={(value) => updateForm('shadow_y', value)} disabled={!form.shadow_enabled} />
                <NumberField label="背景透明度" value={form.background_alpha} min={0} max={220} step={5} onChange={(value) => updateForm('background_alpha', value)} />
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  )
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

/** 数字输入 */
function NumberField({ label, value, min, max, step, disabled, onChange }: { label: string; value: number; min: number; max: number; step: number; disabled?: boolean; onChange: (value: number) => void }) {
  return (
    <label className={`block ${disabled ? 'opacity-45' : ''}`}>
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary disabled:cursor-not-allowed"
      />
    </label>
  )
}

/** 颜色选择 */
function ColorField({ label, value, disabled, onChange }: { label: string; value: string; disabled?: boolean; onChange: (value: string) => void }) {
  return (
    <label className={`block ${disabled ? 'opacity-45' : ''}`}>
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-background-elevated px-2">
        <input
          type="color"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="h-6 w-8 cursor-pointer rounded border border-border bg-transparent disabled:cursor-not-allowed"
        />
        <span className="font-mono text-xs text-foreground-muted">{value}</span>
      </div>
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

/** 分段选择 */
function SegmentedField({ label, value, options, onChange }: { label: string; value: string; options: string[][]; onChange: (value: string) => void }) {
  return (
    <div>
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <div className="grid grid-cols-2 rounded-md border border-border bg-background-elevated p-1">
        {options.map(([optionValue, labelText]) => (
          <button
            key={optionValue}
            type="button"
            onClick={() => onChange(optionValue)}
            className={`h-7 rounded text-xs transition-colors ${
              value === optionValue ? 'bg-primary text-primary-foreground' : 'text-foreground-muted hover:text-foreground'
            }`}
          >
            {labelText}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 开关 */
function ToggleField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex h-9 items-center justify-between rounded-md border border-border bg-background-elevated px-3">
      <span className="text-xs text-foreground-muted">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-primary"
      />
    </label>
  )
}

/** 预览信息 */
function PreviewStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background-elevated p-2">
      <div className="text-[10px] text-foreground-muted">{label}</div>
      <div className="truncate text-xs text-foreground">{value}</div>
    </div>
  )
}
