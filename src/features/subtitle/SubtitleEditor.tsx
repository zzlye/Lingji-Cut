// src/features/subtitle/SubtitleEditor.tsx
// 字幕预设配置面板 - 管理语言、字体、九宫格位置、样式和实时预览
// 交互重做：语言/字号/位置/主色 + 实时预览露出，字体/描边/阴影/一键策略收进高级折叠

import { useEffect, useMemo, useState } from 'react'
import { subtitleApi } from '@/lib/api'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { SubtitlePreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { TextField, SelectField, SegmentedField, SliderField, ColorField, SwitchField, PositionGrid, type FieldOption } from '@/components/fields'

/** 字幕位置类型 */
type SubtitlePosition = SubtitlePreset['position']

/** 字幕表单状态 */
type SubtitlePresetForm = Omit<SubtitlePreset, 'id'>

/** 可选语言配置 */
const LANGUAGE_OPTIONS: FieldOption[] = [
  ['auto', '跟随原视频'], ['zh-CN', '中文 简体'], ['zh-TW', '中文 繁体'], ['en', '英文'], ['ja', '日文'],
  ['ko', '韩文'], ['es', '西班牙语'], ['fr', '法语'], ['de', '德语'], ['vi', '越南语'], ['th', '泰语'], ['custom', '自定义'],
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
  { value: 'top_left', label: '左上' }, { value: 'top', label: '顶部' }, { value: 'top_right', label: '右上' },
  { value: 'middle_left', label: '左中' }, { value: 'center', label: '居中' }, { value: 'middle_right', label: '右中' },
  { value: 'bottom_left', label: '左下' }, { value: 'bottom', label: '底部' }, { value: 'bottom_right', label: '右下' },
]
const POSITION_GRID_OPTIONS: FieldOption[] = POSITION_OPTIONS.map((p) => [p.value, p.label])

/** 快速样式模板 */
const STYLE_TEMPLATES: Array<{ name: string; description: string; patch: Partial<SubtitlePresetForm> }> = [
  { name: '短视频清晰', description: '白字黑边，底部单行', patch: { line_mode: 'single', font_name: 'Source Han Sans SC', font_size: 48, secondary_font_size: 42, font_color: '#FFFFFF', secondary_color: '#FDE68A', outline_color: '#000000', outline_width: 4, shadow_enabled: true, shadow_color: '#000000', shadow_x: 2, shadow_y: 3, background_alpha: 0, position: 'bottom', margin_v: 48 } },
  { name: '电影双语', description: '主副字幕分色，底部留白', patch: { line_mode: 'double', font_name: 'Noto Sans SC', font_size: 42, secondary_font_size: 32, font_color: '#FFFFFF', secondary_color: '#D1D5DB', outline_color: '#111827', outline_width: 3, shadow_enabled: true, shadow_color: '#000000', shadow_x: 1, shadow_y: 2, background_alpha: 0, position: 'bottom', margin_v: 62 } },
  { name: '知识讲解', description: '黄字高亮，适合解说', patch: { line_mode: 'single', font_name: 'Alibaba PuHuiTi', font_size: 50, secondary_font_size: 42, font_color: '#FACC15', secondary_color: '#FFFFFF', outline_color: '#1F2937', outline_width: 4, shadow_enabled: true, shadow_color: '#000000', shadow_x: 2, shadow_y: 3, background_alpha: 0, position: 'bottom', margin_v: 46 } },
  { name: '干净信息条', description: '半透明背景，低描边', patch: { line_mode: 'single', font_name: 'HarmonyOS Sans SC', font_size: 40, secondary_font_size: 34, font_color: '#FFFFFF', secondary_color: '#BAE6FD', outline_color: '#000000', outline_width: 1, shadow_enabled: false, background_alpha: 128, position: 'bottom', margin_v: 36 } },
]

/** 字幕预设默认值 */
function createDefaultForm(name = '短视频清晰字幕'): SubtitlePresetForm {
  return {
    name, is_default: false, line_mode: 'single', language: 'zh-CN', font_name: 'Source Han Sans SC', font_size: 48, secondary_font_size: 42,
    font_color: '#FFFFFF', secondary_color: '#FDE68A', outline_color: '#000000', outline_width: 4,
    shadow_enabled: true, shadow_color: '#000000', shadow_x: 2, shadow_y: 3, background_alpha: 0, position: 'bottom', margin_v: 48,
  }
}

/** 将旧位置值归一到九宫格位置 */
function normalizePosition(position: string): SubtitlePosition {
  if (POSITION_OPTIONS.some((item) => item.value === position)) return position as SubtitlePosition
  return position === 'top' || position === 'center' ? position : 'bottom'
}

/** 将后端预设转换成完整表单，兼容旧数据 */
function presetToForm(preset: SubtitlePreset): SubtitlePresetForm {
  return {
    name: preset.name || '未命名预设', is_default: Boolean(preset.is_default), line_mode: preset.line_mode || 'single',
    language: preset.language || 'auto', font_name: preset.font_name || 'Source Han Sans SC', font_size: preset.font_size || 48, secondary_font_size: preset.secondary_font_size || Math.max(18, Math.round((preset.font_size || 48) * 0.88)),
    font_color: preset.font_color || '#FFFFFF', secondary_color: preset.secondary_color || '#FDE68A', outline_color: preset.outline_color || '#000000',
    outline_width: preset.outline_width ?? 4, shadow_enabled: preset.shadow_enabled ?? true, shadow_color: preset.shadow_color || '#000000',
    shadow_x: preset.shadow_x ?? 2, shadow_y: preset.shadow_y ?? 3, background_alpha: preset.background_alpha ?? 0,
    position: normalizePosition(preset.position), margin_v: preset.margin_v ?? 48,
  }
}

const SUBTITLE_OP_OPTIONS: FieldOption[] = [['none', '不处理'], ['polish', '润色字幕'], ['translate', '翻译字幕'], ['generate', '生成字幕文案']]
const TARGET_LANG_OPTIONS: FieldOption[] = [['zh-CN', '中文 简体'], ['en', '英文'], ['ja', '日文'], ['ko', '韩文'], ['es', '西班牙语'], ['', '跟随字幕']]

/**
 * 字幕预设配置面板
 */
export function SubtitleEditor({ compact = false }: { compact?: boolean }) {
  void compact
  const [presets, setPresets] = useState<SubtitlePreset[]>([])
  const [selectedId, setSelectedId] = useState<number | 'new'>('new')
  const [form, setForm] = useState<SubtitlePresetForm>(() => createDefaultForm())
  const [customLanguage, setCustomLanguage] = useState('')
  const [automationOptions, setAutomationOptions] = useState(() => loadAutomationPreferences())
  const [isSaving, setIsSaving] = useState(false)
  const { addLog } = useTaskStore()

  const usesCustomLanguage = useMemo(() => !LANGUAGE_OPTIONS.some(([value]) => value === form.language), [form.language])
  const selectedLanguage = usesCustomLanguage ? 'custom' : form.language

  const loadPresets = async () => {
    try {
      const data = await subtitleApi.listPresets()
      setPresets(data)
      if (data.length > 0 && selectedId === 'new') {
        const preferred = data.find((p) => p.id === automationOptions.subtitle_preset_id) || data.find((p) => p.is_default) || data[0]
        setSelectedId(preferred.id)
        setForm(presetToForm(preferred))
        setAutomationOptions(saveAutomationPreferences({ subtitle_preset_id: preferred.id, subtitle_language: automationOptions.subtitle_language || preferred.language }))
      }
    } catch (error) {
      addLog('error', `加载字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => { loadPresets() }, [])

  const handleSelectPreset = (id: string) => {
    if (id === 'new') { handleNewPreset(); return }
    const preset = presets.find((item) => item.id === Number(id))
    if (!preset) return
    setSelectedId(preset.id)
    setForm(presetToForm(preset))
    setAutomationOptions(saveAutomationPreferences({ subtitle_preset_id: preset.id, subtitle_language: preset.language || automationOptions.subtitle_language }))
    setCustomLanguage('')
  }

  const handleNewPreset = () => {
    setSelectedId('new')
    setForm(createDefaultForm(`字幕预设 ${presets.length + 1}`))
    setCustomLanguage('')
  }

  const updateForm = <K extends keyof SubtitlePresetForm>(key: K, value: SubtitlePresetForm[K]) => setForm((current) => ({ ...current, [key]: value }))
  const applyTemplate = (patch: Partial<SubtitlePresetForm>) => setForm((current) => ({ ...current, ...patch }))

  const handleSave = async () => {
    const name = form.name.trim()
    if (!name) { addLog('warn', '请输入字幕预设名称'); return }
    const language = selectedLanguage === 'custom' ? customLanguage.trim() || form.language : form.language
    if (!language || language === 'custom') { addLog('warn', '请输入自定义字幕语言'); return }
    setIsSaving(true)
    try {
      const payload = { ...form, name, language, font_size: Number(form.font_size), secondary_font_size: Number(form.secondary_font_size), outline_width: Number(form.outline_width), shadow_x: Number(form.shadow_x), shadow_y: Number(form.shadow_y), background_alpha: Number(form.background_alpha), margin_v: Number(form.margin_v) }
      const saved = selectedId === 'new' ? await subtitleApi.createPreset(payload) : await subtitleApi.updatePreset(selectedId, payload)
      setAutomationOptions(saveAutomationPreferences({ subtitle_preset_id: saved.id, subtitle_language: language }))
      addLog('info', `字幕预设 "${saved.name}" 已保存`)
      setSelectedId(saved.id)
      setForm(presetToForm(saved))
      await loadPresets()
    } catch (error) {
      addLog('error', `保存字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setIsSaving(false) }
  }

  const handleDelete = async () => {
    if (selectedId === 'new') return
    try {
      await subtitleApi.deletePreset(selectedId)
      addLog('info', '字幕预设已删除')
      const next = presets.filter((p) => p.id !== selectedId)
      setPresets(next)
      if (next.length > 0) { setSelectedId(next[0].id); setForm(presetToForm(next[0])) } else { handleNewPreset() }
    } catch (error) {
      addLog('error', `删除字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">字幕设置</h2>
        <p className="text-sm text-muted-foreground">先选快速模板，再微调语言、字号、位置和颜色；右侧实时预览。</p>
      </div>

      {/* 预设工具条 */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-44 flex-1"><SelectField label="已保存预设" value={String(selectedId)} options={[['new', '＋ 新建预设'], ...presets.map((p) => [String(p.id), p.name] as FieldOption)]} onChange={handleSelectPreset} /></div>
        <div className="min-w-44 flex-1"><TextField label="预设名称" value={form.name} onChange={(v) => updateForm('name', v)} /></div>
        {selectedId !== 'new' && <Button variant="outline" className="text-destructive" onClick={handleDelete}>删除</Button>}
        <Button onClick={handleSave} disabled={isSaving}>{isSaving ? '保存中…' : '保存预设'}</Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-5">
          {/* 快速模板 */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {STYLE_TEMPLATES.map((template) => (
              <button key={template.name} onClick={() => applyTemplate(template.patch)} className="rounded-lg border bg-card p-3 text-left transition-colors hover:border-primary hover:bg-primary/5">
                <p className="text-sm font-medium">{template.name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{template.description}</p>
              </button>
            ))}
          </div>

          {/* 常用 */}
          <Card>
            <CardHeader><CardTitle className="text-sm">常用</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <SelectField label="字幕语言" value={selectedLanguage} options={LANGUAGE_OPTIONS} onChange={(v) => { updateForm('language', v); if (v !== 'custom') setCustomLanguage('') }} />
                {selectedLanguage === 'custom' && (
                  <TextField label="自定义语言" value={customLanguage || (usesCustomLanguage ? form.language : '')} placeholder="例如 pt-BR / vi / th" onChange={(v) => { setCustomLanguage(v); updateForm('language', v || 'custom') }} />
                )}
                <SegmentedField label="字幕行数" value={form.line_mode} options={[['single', '单行'], ['double', '双行']]} onChange={(v) => updateForm('line_mode', v as 'single' | 'double')} />
              </div>
              <SliderField label="主字幕大小" value={form.font_size} min={18} max={96} step={1} suffix=" px" onChange={(v) => updateForm('font_size', v)} />
              {form.line_mode === 'double' && <SliderField label="第二行大小" value={form.secondary_font_size} min={18} max={96} step={1} suffix=" px" onChange={(v) => updateForm('secondary_font_size', v)} />}
              <div>
                <p className="mb-2 text-sm">字幕位置</p>
                <PositionGrid value={form.position} options={POSITION_GRID_OPTIONS} onChange={(v) => updateForm('position', v as SubtitlePosition)} />
              </div>
              <ColorField label="主字幕颜色" value={form.font_color} onChange={(v) => updateForm('font_color', v)} />
            </CardContent>
          </Card>

          {/* 高级 */}
          <Accordion type="multiple" className="space-y-2">
            <AccordionItem value="font" className="rounded-lg border px-4">
              <AccordionTrigger className="text-sm">字体</AccordionTrigger>
              <AccordionContent className="space-y-3 pb-3">
                <TextField label="字体名称" value={form.font_name} onChange={(v) => updateForm('font_name', v)} description="渲染效果取决于本机是否安装该字体" />
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {FONT_PRESETS.map((font) => (
                    <button key={font.family} onClick={() => updateForm('font_name', font.family)} style={{ fontFamily: font.family }}
                      className={cn('rounded-md border px-2.5 py-2 text-left text-xs transition-colors', form.font_name === font.family ? 'border-primary bg-primary/10 text-primary' : 'bg-card text-muted-foreground hover:border-primary/50 hover:text-foreground')}>
                      <span className="block truncate font-medium">{font.name}</span>
                    </button>
                  ))}
                </div>
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="style" className="rounded-lg border px-4">
              <AccordionTrigger className="text-sm">颜色 / 描边 / 阴影</AccordionTrigger>
              <AccordionContent className="space-y-3 pb-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <ColorField label="第二行颜色" value={form.secondary_color} onChange={(v) => updateForm('secondary_color', v)} />
                  <ColorField label="描边颜色" value={form.outline_color} onChange={(v) => updateForm('outline_color', v)} />
                </div>
                <SliderField label="描边宽度" value={form.outline_width} min={0} max={10} step={1} onChange={(v) => updateForm('outline_width', v)} />
                <SwitchField label="启用阴影" checked={form.shadow_enabled} onChange={(v) => updateForm('shadow_enabled', v)} />
                {form.shadow_enabled && (
                  <>
                    <ColorField label="阴影颜色" value={form.shadow_color} onChange={(v) => updateForm('shadow_color', v)} />
                    <div className="grid gap-3 sm:grid-cols-2">
                      <SliderField label="阴影 X" value={form.shadow_x} min={-12} max={12} step={1} onChange={(v) => updateForm('shadow_x', v)} />
                      <SliderField label="阴影 Y" value={form.shadow_y} min={-12} max={12} step={1} onChange={(v) => updateForm('shadow_y', v)} />
                    </div>
                  </>
                )}
                <SliderField label="背景底板透明度" value={form.background_alpha} min={0} max={255} step={5} description="0 为无底板，越大底板越不透明" onChange={(v) => updateForm('background_alpha', v)} />
                <SliderField label="屏幕边距" value={form.margin_v} min={0} max={180} step={2} suffix=" px" onChange={(v) => updateForm('margin_v', v)} />
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="auto" className="rounded-lg border px-4">
              <AccordionTrigger className="text-sm">一键完成字幕策略</AccordionTrigger>
              <AccordionContent className="space-y-3 pb-3">
                <SelectField label="字幕识别方式" value={automationOptions.subtitle_recognition_mode} options={[{ value: 'local', label: '本地识别（快·免费）' }, { value: 'gemini_full', label: 'Gemini 转写（最准·较慢·走文本API）' }, { value: 'gemini_align', label: 'Gemini 内容+本地时间轴' }]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ subtitle_recognition_mode: v as typeof automationOptions.subtitle_recognition_mode }))} description="Gemini 模式识别更准但更慢，需先在「文本 API」配置 Gemini 渠道" />
                <SelectField label="文本 API 处理" value={automationOptions.subtitle_operation} options={SUBTITLE_OP_OPTIONS} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ subtitle_operation: v as typeof automationOptions.subtitle_operation }))} description="需先在「文本 API」配置渠道" />
                <SelectField label="输出语言" value={automationOptions.subtitle_target_language || ''} options={TARGET_LANG_OPTIONS} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ subtitle_target_language: v }))} />
                <SwitchField label="默认烧录硬字幕" description="导出时把字幕烧进画面" checked={automationOptions.burn_subtitles} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ burn_subtitles: v }))} />
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>

        {/* 实时预览 */}
        <aside className="min-w-0">
          <div className="sticky top-2">
            <p className="mb-2 text-sm font-medium">实时预览</p>
            <PreviewBox form={form} languageLabel={selectedLanguage === 'custom' ? customLanguage || '自定义' : selectedLanguage} />
          </div>
        </aside>
      </div>
    </div>
  )
}

/** 预览框 */
function PreviewBox({ form, languageLabel }: { form: SubtitlePresetForm; languageLabel: string }) {
  const previewFontSize = Math.max(14, Math.min(30, Number(form.font_size) * 0.44))
  const secondaryPreviewFontSize = Math.max(12, Math.min(28, Number(form.secondary_font_size || form.font_size) * 0.44))
  const backgroundAlpha = Math.max(0, Math.min(255, Number(form.background_alpha) || 0))
  const previewBackground = `rgba(0, 0, 0, ${backgroundAlpha / 255})`
  const positionClass = previewPositionClass(form.position)
  const previewTextShadow = buildPreviewTextShadow(form)

  return (
    <div className="space-y-3">
      <div className="aspect-[9/16] max-h-[460px] overflow-hidden rounded-lg border border-border-bright bg-[linear-gradient(145deg,#0f172a_0%,#1d4ed8_45%,#7c2d12_100%)]">
        <div className={`flex h-full p-5 ${positionClass}`}>
          <div className="max-w-full rounded px-3 py-2 text-center leading-tight" style={{ background: previewBackground, color: form.font_color, fontFamily: form.font_name, fontSize: `${previewFontSize}px`, textShadow: previewTextShadow, lineHeight: 1.18 }}>
            <div className="break-words">主字幕预览文本</div>
            {form.line_mode === 'double' && <div className="mt-1 break-words" style={{ color: form.secondary_color, fontSize: `${secondaryPreviewFontSize}px` }}>Second subtitle line</div>}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
        <PreviewStat label="语言" value={languageLabel} />
        <PreviewStat label="位置" value={positionLabel(form.position)} />
        <PreviewStat label="字体" value={form.font_name} />
        <PreviewStat label="字号" value={form.line_mode === 'double' ? `${form.font_size}/${form.secondary_font_size}px` : `${form.font_size}px`} />
      </div>
    </div>
  )
}

/** 预览信息 */
function PreviewStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border bg-card p-2">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="truncate text-xs text-foreground">{value}</div>
    </div>
  )
}

/** 字幕预览位置 */
function previewPositionClass(position: SubtitlePosition) {
  const classes: Record<SubtitlePosition, string> = {
    top_left: 'items-start justify-start', top: 'items-start justify-center', top_right: 'items-start justify-end',
    middle_left: 'items-center justify-start', center: 'items-center justify-center', middle_right: 'items-center justify-end',
    bottom_left: 'items-end justify-start', bottom: 'items-end justify-center', bottom_right: 'items-end justify-end',
  }
  return classes[position] || classes.bottom
}

/** 用多方向阴影模拟字幕描边 */
function buildPreviewTextShadow(form: SubtitlePresetForm) {
  const outline = Math.max(0, Number(form.outline_width) * 0.38)
  const outlineShadow = outline > 0
    ? [`${outline}px 0 ${form.outline_color}`, `-${outline}px 0 ${form.outline_color}`, `0 ${outline}px ${form.outline_color}`, `0 -${outline}px ${form.outline_color}`, `${outline}px ${outline}px ${form.outline_color}`, `-${outline}px ${outline}px ${form.outline_color}`, `${outline}px -${outline}px ${form.outline_color}`, `-${outline}px -${outline}px ${form.outline_color}`]
    : []
  const dropShadow = form.shadow_enabled ? [`${form.shadow_x}px ${form.shadow_y}px 3px ${form.shadow_color}`] : []
  return [...outlineShadow, ...dropShadow].join(', ') || 'none'
}

/** 字幕位置文案 */
function positionLabel(position: SubtitlePosition) {
  return POSITION_OPTIONS.find((option) => option.value === position)?.label || '底部'
}
