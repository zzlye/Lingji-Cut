// src/features/subtitle/SubtitleEditor.tsx
// 字幕预设配置面板 - 管理语言、字体预设、九宫格位置、样式和实时预览

import { useEffect, useMemo, useState } from 'react'
import { subtitleApi } from '@/lib/api'
import type { SubtitlePreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/** 字幕位置类型 */
type SubtitlePosition = SubtitlePreset['position']

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
  ['vi', '越南语'],
  ['th', '泰语'],
  ['custom', '自定义'],
]

/** 免费或常见可商用字体预设，最终渲染取决于本机是否安装对应字体 */
const FONT_PRESETS = [
  { name: '思源黑体', family: 'Source Han Sans SC' },
  { name: 'Noto Sans SC', family: 'Noto Sans SC' },
  { name: '阿里巴巴普惠体', family: 'Alibaba PuHuiTi' },
  { name: 'HarmonyOS Sans', family: 'HarmonyOS Sans SC' },
  { name: 'MiSans', family: 'MiSans' },
  { name: '霞鹜文楷', family: 'LXGW WenKai' },
  { name: '思源宋体', family: 'Source Han Serif SC' },
  { name: '微软雅黑', family: 'Microsoft YaHei' },
]

/** 九宫格字幕位置 */
const POSITION_OPTIONS: Array<{ value: SubtitlePosition; label: string }> = [
  { value: 'top_left', label: '左上' },
  { value: 'top', label: '顶部' },
  { value: 'top_right', label: '右上' },
  { value: 'middle_left', label: '左中' },
  { value: 'center', label: '居中' },
  { value: 'middle_right', label: '右中' },
  { value: 'bottom_left', label: '左下' },
  { value: 'bottom', label: '底部' },
  { value: 'bottom_right', label: '右下' },
]

/** 快速样式模板 */
const STYLE_TEMPLATES: Array<{ name: string; description: string; patch: Partial<SubtitlePresetForm> }> = [
  {
    name: '短视频清晰',
    description: '白字黑边，底部双行',
    patch: {
      line_mode: 'double',
      font_name: 'Source Han Sans SC',
      font_size: 48,
      font_color: '#FFFFFF',
      secondary_color: '#FDE68A',
      outline_color: '#000000',
      outline_width: 4,
      shadow_enabled: true,
      shadow_color: '#000000',
      shadow_x: 2,
      shadow_y: 3,
      background_alpha: 0,
      position: 'bottom',
      margin_v: 48,
    },
  },
  {
    name: '电影双语',
    description: '主副字幕分色，底部留白',
    patch: {
      line_mode: 'double',
      font_name: 'Noto Sans SC',
      font_size: 42,
      font_color: '#FFFFFF',
      secondary_color: '#D1D5DB',
      outline_color: '#111827',
      outline_width: 3,
      shadow_enabled: true,
      shadow_color: '#000000',
      shadow_x: 1,
      shadow_y: 2,
      background_alpha: 0,
      position: 'bottom',
      margin_v: 62,
    },
  },
  {
    name: '知识讲解',
    description: '黄字高亮，适合解说',
    patch: {
      line_mode: 'single',
      font_name: 'Alibaba PuHuiTi',
      font_size: 50,
      font_color: '#FACC15',
      secondary_color: '#FFFFFF',
      outline_color: '#1F2937',
      outline_width: 4,
      shadow_enabled: true,
      shadow_color: '#000000',
      shadow_x: 2,
      shadow_y: 3,
      background_alpha: 0,
      position: 'bottom',
      margin_v: 46,
    },
  },
  {
    name: '干净信息条',
    description: '半透明背景，低描边',
    patch: {
      line_mode: 'single',
      font_name: 'HarmonyOS Sans SC',
      font_size: 40,
      font_color: '#FFFFFF',
      secondary_color: '#BAE6FD',
      outline_color: '#000000',
      outline_width: 1,
      shadow_enabled: false,
      background_alpha: 128,
      position: 'bottom',
      margin_v: 36,
    },
  },
]

/** 字幕预设默认值 */
function createDefaultForm(name = '短视频清晰字幕'): SubtitlePresetForm {
  return {
    name,
    is_default: false,
    line_mode: 'double',
    language: 'zh-CN',
    font_name: 'Source Han Sans SC',
    font_size: 48,
    font_color: '#FFFFFF',
    secondary_color: '#FDE68A',
    outline_color: '#000000',
    outline_width: 4,
    shadow_enabled: true,
    shadow_color: '#000000',
    shadow_x: 2,
    shadow_y: 3,
    background_alpha: 0,
    position: 'bottom',
    margin_v: 48,
  }
}

/** 将旧位置值归一到九宫格位置 */
function normalizePosition(position: string): SubtitlePosition {
  if (POSITION_OPTIONS.some((item) => item.value === position)) {
    return position as SubtitlePosition
  }
  return position === 'top' || position === 'center' ? position : 'bottom'
}

/** 将后端预设转换成完整表单，兼容旧数据 */
function presetToForm(preset: SubtitlePreset): SubtitlePresetForm {
  return {
    name: preset.name || '未命名预设',
    is_default: Boolean(preset.is_default),
    line_mode: preset.line_mode || 'double',
    language: preset.language || 'auto',
    font_name: preset.font_name || 'Source Han Sans SC',
    font_size: preset.font_size || 48,
    font_color: preset.font_color || '#FFFFFF',
    secondary_color: preset.secondary_color || '#FDE68A',
    outline_color: preset.outline_color || '#000000',
    outline_width: preset.outline_width ?? 4,
    shadow_enabled: preset.shadow_enabled ?? true,
    shadow_color: preset.shadow_color || '#000000',
    shadow_x: preset.shadow_x ?? 2,
    shadow_y: preset.shadow_y ?? 3,
    background_alpha: preset.background_alpha ?? 0,
    position: normalizePosition(preset.position),
    margin_v: preset.margin_v ?? 48,
  }
}

/**
 * 字幕预设配置面板
 * 使用流式布局：顶部预设工具条、左侧参数、右侧实时预览。
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
  const selectedLanguage = usesCustomLanguage ? 'custom' : form.language

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
  const handleSelectPreset = (id: string) => {
    if (id === 'new') {
      handleNewPreset()
      return
    }
    const preset = presets.find((item) => item.id === Number(id))
    if (!preset) return
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

  /** 应用快速样式模板 */
  const applyTemplate = (patch: Partial<SubtitlePresetForm>) => {
    setForm((current) => ({ ...current, ...patch }))
  }

  /** 保存当前预设 */
  const handleSave = async () => {
    const name = form.name.trim()
    if (!name) {
      addLog('warn', '请输入字幕预设名称')
      return
    }

    const language = selectedLanguage === 'custom' ? customLanguage.trim() || form.language : form.language
    if (!language || language === 'custom') {
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
  const handleDelete = async () => {
    if (selectedId === 'new') return
    try {
      await subtitleApi.deletePreset(selectedId)
      addLog('info', '字幕预设已删除')
      const nextPresets = presets.filter((preset) => preset.id !== selectedId)
      setPresets(nextPresets)
      if (nextPresets.length > 0) {
        setSelectedId(nextPresets[0].id)
        setForm(presetToForm(nextPresets[0]))
      } else {
        handleNewPreset()
      }
    } catch (error) {
      addLog('error', `删除字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!compact && (
        <div className="border-b border-border px-4 py-3">
          <h3 className="text-sm font-medium">字幕设置</h3>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto flex max-w-7xl flex-col gap-4">
          <section className="rounded-lg border border-border bg-background p-3">
            <div className="grid grid-cols-[minmax(180px,260px)_minmax(180px,1fr)_auto] items-end gap-3 max-lg:grid-cols-1">
              <SelectField
                label="已保存预设"
                value={String(selectedId)}
                options={[['new', '新建预设'], ...presets.map((preset) => [String(preset.id), preset.name])]}
                onChange={handleSelectPreset}
              />
              <TextField label="预设名称" value={form.name} onChange={(value) => updateForm('name', value)} />
              <div className="flex flex-wrap gap-2">
                <button onClick={handleNewPreset} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5">
                  新建
                </button>
                {selectedId !== 'new' && (
                  <button onClick={handleDelete} className="h-9 rounded-md border border-border px-4 text-sm text-destructive hover:bg-white/5">
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
            </div>
          </section>

          <div className="grid grid-cols-[minmax(0,1fr)_minmax(300px,340px)] gap-4 max-lg:grid-cols-1">
            <main className="min-w-0 space-y-4">
              <section className="rounded-lg border border-border bg-background p-4">
                <SectionTitle title="快速模板" description="先选接近的风格，再微调颜色、位置和字号。" />
                <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
                  {STYLE_TEMPLATES.map((template) => (
                    <button
                      key={template.name}
                      onClick={() => applyTemplate(template.patch)}
                      className="rounded-md border border-border bg-background-elevated p-3 text-left transition-colors hover:border-border-bright hover:bg-white/5"
                    >
                      <div className="text-sm font-medium">{template.name}</div>
                      <div className="mt-1 text-xs text-foreground-muted">{template.description}</div>
                    </button>
                  ))}
                </div>
              </section>

              <section className="rounded-lg border border-border bg-background p-4">
                <SectionTitle title="语言和排版" description="配置字幕语言、单双行、九宫格位置和屏幕安全边距。" />
                <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-3">
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
                  <NumberField label="屏幕边距" value={form.margin_v} min={0} max={180} step={2} onChange={(value) => updateForm('margin_v', value)} />
                </div>

                <div className="mt-4">
                  <span className="mb-2 block text-xs text-foreground-muted">字幕位置</span>
                  <PositionGrid value={form.position} onChange={(value) => updateForm('position', value)} />
                </div>
              </section>

              <section className="rounded-lg border border-border bg-background p-4">
                <SectionTitle title="字体和颜色" description="内置免费字体预设，同时保留手动输入字体名。" />
                <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-3">
                  <TextField label="字体名称" value={form.font_name} onChange={(value) => updateForm('font_name', value)} />
                  <NumberField label="文字大小" value={form.font_size} min={18} max={96} step={1} onChange={(value) => updateForm('font_size', value)} />
                  <ColorField label="主字幕颜色" value={form.font_color} onChange={(value) => updateForm('font_color', value)} />
                  <ColorField label="第二行颜色" value={form.secondary_color} onChange={(value) => updateForm('secondary_color', value)} />
                </div>
                <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
                  {FONT_PRESETS.map((font) => (
                    <button
                      key={font.family}
                      onClick={() => updateForm('font_name', font.family)}
                      className={`min-h-10 rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                        form.font_name === font.family
                          ? 'border-primary bg-primary/10 text-primary'
                          : 'border-border bg-background-elevated text-foreground-muted hover:border-border-bright hover:text-foreground'
                      }`}
                      style={{ fontFamily: font.family }}
                    >
                      <div className="truncate font-medium">{font.name}</div>
                      <div className="truncate text-[10px] opacity-75">{font.family}</div>
                    </button>
                  ))}
                </div>
              </section>

              <section className="rounded-lg border border-border bg-background p-4">
                <SectionTitle title="描边、阴影和底板" description="控制字幕在复杂画面上的可读性。" />
                <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-3">
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

            <aside className="min-w-0">
              <div className="sticky top-0 rounded-lg border border-border bg-background p-4">
                <div className="flex items-center justify-between gap-3">
                  <SectionTitle title="实时预览" description="预览位置、字号、颜色、描边和阴影。" />
                  <button
                    onClick={() => setIsPreviewOpen((value) => !value)}
                    className="h-8 shrink-0 rounded-md border border-border px-3 text-xs hover:bg-white/5"
                  >
                    {isPreviewOpen ? '收起' : '展开'}
                  </button>
                </div>

                {isPreviewOpen && (
                  <PreviewBox
                    form={form}
                    languageLabel={selectedLanguage === 'custom' ? customLanguage || '自定义' : selectedLanguage}
                  />
                )}
              </div>
            </aside>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 分组标题 */
function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div className="min-w-0">
      <h4 className="text-sm font-medium">{title}</h4>
      <p className="mt-1 text-xs text-foreground-muted">{description}</p>
    </div>
  )
}

/** 预览框 */
function PreviewBox({ form, languageLabel }: { form: SubtitlePresetForm; languageLabel: string }) {
  const previewFontSize = Math.max(14, Math.min(30, Number(form.font_size) * 0.44))
  const backgroundAlpha = Math.max(0, Math.min(255, Number(form.background_alpha) || 0))
  const previewBackground = `rgba(0, 0, 0, ${backgroundAlpha / 255})`
  const positionClass = previewPositionClass(form.position)
  const previewTextShadow = buildPreviewTextShadow(form)

  return (
    <div className="mt-4 space-y-3">
      <div className="aspect-[9/16] max-h-[460px] overflow-hidden rounded-lg border border-border-bright bg-[linear-gradient(145deg,#0f172a_0%,#1d4ed8_45%,#7c2d12_100%)]">
        <div className={`flex h-full p-5 ${positionClass}`}>
          <div
            className="max-w-full rounded px-3 py-2 text-center leading-tight"
            style={{
              background: previewBackground,
              color: form.font_color,
              fontFamily: form.font_name,
              fontSize: `${previewFontSize}px`,
              textShadow: previewTextShadow,
              lineHeight: 1.18,
            }}
          >
            <div className="break-words">主字幕预览文本</div>
            {form.line_mode === 'double' && (
              <div className="mt-1 break-words" style={{ color: form.secondary_color }}>
                Second subtitle line
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs text-foreground-muted">
        <PreviewStat label="语言" value={languageLabel} />
        <PreviewStat label="位置" value={positionLabel(form.position)} />
        <PreviewStat label="字体" value={form.font_name} />
        <PreviewStat label="字号" value={`${form.font_size}px`} />
      </div>
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
        <span className="min-w-0 truncate font-mono text-xs text-foreground-muted">{value}</span>
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

/** 九宫格位置选择 */
function PositionGrid({ value, onChange }: { value: SubtitlePosition; onChange: (value: SubtitlePosition) => void }) {
  return (
    <div className="grid max-w-md grid-cols-3 gap-2">
      {POSITION_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`h-10 rounded-md border text-xs transition-colors ${
            value === option.value
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-border bg-background-elevated text-foreground-muted hover:border-border-bright hover:text-foreground'
          }`}
        >
          {option.label}
        </button>
      ))}
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

/** 字幕预览位置 */
function previewPositionClass(position: SubtitlePosition) {
  const classes: Record<SubtitlePosition, string> = {
    top_left: 'items-start justify-start',
    top: 'items-start justify-center',
    top_right: 'items-start justify-end',
    middle_left: 'items-center justify-start',
    center: 'items-center justify-center',
    middle_right: 'items-center justify-end',
    bottom_left: 'items-end justify-start',
    bottom: 'items-end justify-center',
    bottom_right: 'items-end justify-end',
  }
  return classes[position] || classes.bottom
}

/** 用多方向阴影模拟字幕描边，避免浏览器 stroke 把预览文字压黑 */
function buildPreviewTextShadow(form: SubtitlePresetForm) {
  const outline = Math.max(0, Number(form.outline_width) * 0.38)
  const outlineShadow = outline > 0
    ? [
      `${outline}px 0 ${form.outline_color}`,
      `-${outline}px 0 ${form.outline_color}`,
      `0 ${outline}px ${form.outline_color}`,
      `0 -${outline}px ${form.outline_color}`,
      `${outline}px ${outline}px ${form.outline_color}`,
      `-${outline}px ${outline}px ${form.outline_color}`,
      `${outline}px -${outline}px ${form.outline_color}`,
      `-${outline}px -${outline}px ${form.outline_color}`,
    ]
    : []
  const dropShadow = form.shadow_enabled
    ? [`${form.shadow_x}px ${form.shadow_y}px 3px ${form.shadow_color}`]
    : []
  return [...outlineShadow, ...dropShadow].join(', ') || 'none'
}

/** 字幕位置文案 */
function positionLabel(position: SubtitlePosition) {
  return POSITION_OPTIONS.find((option) => option.value === position)?.label || '底部'
}
