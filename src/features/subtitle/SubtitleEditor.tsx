// src/features/subtitle/SubtitleEditor.tsx
// 字幕预设配置面板 - 管理语言、字体、九宫格位置、样式和实时预览
// 交互重做：语言/字号/位置/主色 + 实时预览露出，字体/描边/阴影/一键策略收进高级折叠

import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronDown, Download, Loader2, Search, Type } from 'lucide-react'
import { subtitleApi } from '@/lib/api'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { SubtitlePreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { TextField, SelectField, SegmentedField, SliderField, ColorField, SwitchField, PositionGrid, type FieldOption } from '@/components/fields'

/** 字幕位置类型 */
type SubtitlePosition = SubtitlePreset['position']

/** 字幕表单状态 */
type SubtitlePresetForm = Omit<SubtitlePreset, 'id'>

/** 字体授权类型 */
type FontLicenseKind = 'free' | 'commercial' | 'system'

/** 字体筛选类型 */
type FontFilterKind = 'all' | FontLicenseKind

/** 字体库条目；这里只保存字体名，不内置或分发字体文件 */
type FontPreset = {
  name: string
  family: string
  license: FontLicenseKind
  note: string
  aliases?: string[]
  installable?: boolean
}

/** 可选语言配置 */
const LANGUAGE_OPTIONS: FieldOption[] = [
  ['auto', '跟随原视频'], ['zh-CN', '中文 简体'], ['zh-TW', '中文 繁体'], ['en', '英文'], ['ja', '日文'],
  ['ko', '韩文'], ['es', '西班牙语'], ['fr', '法语'], ['de', '德语'], ['vi', '越南语'], ['th', '泰语'], ['custom', '自定义'],
]

/** 字体授权标签样式 */
const FONT_LICENSE_META: Record<FontLicenseKind, { label: string; className: string }> = {
  free: { label: '免费可商用', className: 'border-success/40 bg-success/10 text-success' },
  commercial: { label: '商业字体', className: 'border-warning/50 bg-warning/10 text-warning' },
  system: { label: '随系统授权', className: 'border-info/40 bg-info/10 text-info' },
}

/** 字体分组；实际渲染取决于本机是否安装对应字体 */
const FONT_GROUPS: Array<{ title: string; description: string; fonts: FontPreset[] }> = [
  {
    title: '免费可商用',
    description: '适合默认优先使用，仍需按字体官网协议安装和使用。',
    fonts: [
      { name: '思源黑体', family: 'Source Han Sans SC', license: 'free', note: 'Adobe / Google 开源 CJK 黑体', installable: true },
      { name: '思源宋体', family: 'Source Han Serif SC', license: 'free', note: '开源 CJK 宋体', installable: true },
      { name: 'Noto Sans SC', family: 'Noto Sans SC', license: 'free', note: 'Google Fonts 简中黑体', installable: true },
      { name: 'Noto Serif SC', family: 'Noto Serif SC', license: 'free', note: 'Google Fonts 简中宋体', installable: true },
      { name: 'Noto Sans CJK SC', family: 'Noto Sans CJK SC', license: 'free', note: 'Noto CJK 本地安装名', installable: true },
      { name: '阿里巴巴普惠体', family: 'Alibaba PuHuiTi', license: 'free', note: '电商和短视频常用黑体' },
      { name: '阿里巴巴普惠体 2.0', family: 'Alibaba PuHuiTi 2.0', license: 'free', note: '新版普惠体安装名' },
      { name: 'MiSans', family: 'MiSans', license: 'free', note: '小米字体，作品使用需遵守官方协议' },
      { name: 'HarmonyOS Sans', family: 'HarmonyOS Sans SC', license: 'free', note: '华为字体，作品使用需遵守官方协议' },
      { name: '霞鹜文楷', family: 'LXGW WenKai', license: 'free', note: '开源手写楷体风格', installable: true },
      { name: '得意黑', family: 'Smiley Sans', license: 'free', note: '标题感强，适合短视频封面字幕' },
      { name: '站酷庆科黄油体', family: 'ZCOOL QingKe HuangYou', license: 'free', note: '偏标题和轻松风格', installable: true },
      { name: '站酷快乐体', family: 'ZCOOL KuaiLe', license: 'free', note: '活泼标题字', installable: true },
      { name: '站酷小薇体', family: 'ZCOOL XiaoWei', license: 'free', note: '宋体标题风格', installable: true },
      { name: '马善政毛笔', family: 'Ma Shan Zheng', license: 'free', note: '毛笔风格，适合少量强调', installable: true },
      { name: '龙藏体', family: 'Long Cang', license: 'free', note: '书法风格，适合片头短句', installable: true },
      { name: '志莽行书', family: 'Zhi Mang Xing', license: 'free', note: '行书风格，适合装饰性字幕', installable: true },
      { name: 'M PLUS Rounded 1c', family: 'M PLUS Rounded 1c', license: 'free', note: '圆角日文字体', installable: true },
      { name: 'Zen Maru Gothic', family: 'Zen Maru Gothic', license: 'free', note: '日文圆体', installable: true },
    ],
  },
  {
    title: '商业字体 / 需授权',
    description: '软件内可以直接选择；公开视频、商单或账号运营发布前请自行确认授权。',
    fonts: [
      { name: '方正兰亭黑', family: '方正兰亭黑', license: 'commercial', note: '方正系商业字体，常见安装名也可能是 FZLanTingHei', aliases: ['FZLanTingHei'] },
      { name: '方正黑体', family: '方正黑体', license: 'commercial', note: '方正系商业字体，常见安装名也可能是 FZHei-B01', aliases: ['FZHei-B01'] },
      { name: '方正综艺', family: '方正综艺', license: 'commercial', note: '方正标题字体，安装名可能带简体/繁体后缀', aliases: ['FZZongYi-M05'] },
      { name: '汉仪旗黑', family: '汉仪旗黑', license: 'commercial', note: '汉仪系商业字体，安装名可能带 W 或版本后缀', aliases: ['HYQiHei'] },
      { name: '汉仪中黑', family: '汉仪中黑', license: 'commercial', note: '汉仪系商业字体，安装名可能带 W 或版本后缀', aliases: ['HYZhongHei'] },
      { name: '汉仪雅酷黑', family: '汉仪雅酷黑', license: 'commercial', note: '汉仪系商业字体，安装名可能带 W 或版本后缀', aliases: ['HYYaKuHei'] },
      { name: '造字工房悦黑', family: '造字工房悦黑', license: 'commercial', note: '造字工房商业字体，按安装后的字体全名为准', aliases: ['ZaoZiGongFangYueHei'] },
      { name: '造字工房朗倩', family: '造字工房朗倩', license: 'commercial', note: '造字工房商业字体，按安装后的字体全名为准', aliases: ['ZaoZiGongFangLangQian'] },
      { name: '华康黑体', family: '华康黑体', license: 'commercial', note: '华康系商业字体，安装名可能是 DFHei 或 DFPHei', aliases: ['DFHei', 'DFPHei'] },
      { name: '蒙纳黑体', family: '蒙纳黑体', license: 'commercial', note: '蒙纳系商业字体，按安装后的字体全名为准', aliases: ['MHei'] },
      { name: '文鼎黑体', family: '文鼎黑体', license: 'commercial', note: '文鼎系商业字体，按安装后的字体全名为准', aliases: ['AR Hei'] },
      { name: '字魂字体', family: '字魂字体', license: 'commercial', note: '字魂字体需按套餐授权，请先在系统中安装后再选择', aliases: ['ZiHun'] },
    ],
  },
  {
    title: '系统自带 / 随系统授权',
    description: '通常不能复制分发字体文件；用来烧录本机视频前请确认系统或软件授权。',
    fonts: [
      { name: '微软雅黑', family: 'Microsoft YaHei', license: 'system', note: 'Windows 简中常见字体' },
      { name: '微软正黑体', family: 'Microsoft JhengHei', license: 'system', note: 'Windows 繁中常见字体' },
      { name: '黑体', family: 'SimHei', license: 'system', note: 'Windows 传统黑体' },
      { name: '宋体', family: 'SimSun', license: 'system', note: 'Windows 传统宋体' },
      { name: '等线', family: 'DengXian', license: 'system', note: 'Windows 现代黑体' },
      { name: '楷体', family: 'KaiTi', license: 'system', note: 'Windows 楷体' },
      { name: '仿宋', family: 'FangSong', license: 'system', note: 'Windows 仿宋' },
      { name: '苹方', family: 'PingFang SC', license: 'system', note: 'macOS / iOS 简中系统字体' },
      { name: '冬青黑体', family: 'Hiragino Sans GB', license: 'system', note: 'macOS 常见中文黑体' },
      { name: '华文黑体', family: 'STHeiti', license: 'system', note: 'macOS 常见中文字体' },
      { name: '游ゴシック', family: 'Yu Gothic', license: 'system', note: 'Windows / macOS 日文字体' },
      { name: 'Meiryo', family: 'Meiryo', license: 'system', note: 'Windows 日文字体' },
      { name: 'Malgun Gothic', family: 'Malgun Gothic', license: 'system', note: 'Windows 韩文字体' },
    ],
  },
]
const FONT_LIBRARY = FONT_GROUPS.flatMap((group) => group.fonts)

/** 字体列表筛选 */
const FONT_FILTER_OPTIONS: FieldOption[] = [['all', '全部'], ['free', '免费'], ['commercial', '商业'], ['system', '系统']]

/** 预览备用字体，避免目标字体不存在时右侧预览空白或只显示方块 */
const FONT_FALLBACKS = ['Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', 'Source Han Sans SC', 'SimHei', 'Arial', 'sans-serif']

/** 通用 CSS 字体族，不需要加引号 */
const GENERIC_FONT_FAMILIES = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui'])

/** 字体名称归一化，用于查找和检测缓存 */
function fontKey(family: string) {
  return family.trim().toLowerCase()
}

/** 为 CSS font-family 安全加引号，处理中文、空格和引号 */
function cssFontName(family: string) {
  const clean = family.trim()
  if (!clean) return ''
  if (GENERIC_FONT_FAMILIES.has(clean.toLowerCase())) return clean
  return `"${clean.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
}

/** 生成带中文 fallback 的字体栈，避免预览因为目标字体缺失而不可读 */
function buildCssFontFamily(family: string) {
  const names = [family.trim(), ...FONT_FALLBACKS].filter(Boolean)
  const uniqueNames = Array.from(new Set(names.map((name) => name.trim()))).filter(Boolean)
  return uniqueNames.map(cssFontName).join(', ')
}

/** 查找字体库条目，兼容用户保存过旧别名的情况 */
function findFontPreset(family: string) {
  const key = fontKey(family)
  if (!key) return undefined
  return FONT_LIBRARY.find((font) => fontKey(font.family) === key || font.aliases?.some((alias) => fontKey(alias) === key))
}

/** 用 canvas 宽度差异粗略判断本机是否有该字体；检测不到也允许用户继续选择 */
function detectLocalFont(family: string) {
  const clean = family.trim()
  if (!clean) return false
  if (GENERIC_FONT_FAMILIES.has(clean.toLowerCase())) return true
  if (typeof document === 'undefined') return false

  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  if (!context) return false

  const sample = 'BESbswy 中文字幕预览 12345 テスト'
  const baseFamilies = ['serif', 'sans-serif', 'monospace']
  const measure = (fontFamily: string) => {
    context.font = `72px ${fontFamily}`
    return context.measureText(sample).width
  }

  return baseFamilies.some((baseFamily) => {
    const baseWidth = measure(baseFamily)
    const testWidth = measure(`${cssFontName(clean)}, ${baseFamily}`)
    return Math.abs(testWidth - baseWidth) > 0.1
  })
}

/** 检测字体列表中哪些在本机可用，辅助解释“选了但不生效”的情况 */
function useFontAvailability(families: string[], refreshKey = 0) {
  const familyKey = useMemo(() => Array.from(new Set(families.map(fontKey).filter(Boolean))).join('|'), [families])
  const [availability, setAvailability] = useState<Record<string, boolean>>({})

  useEffect(() => {
    const uniqueFamilies = Array.from(new Set(families.map((family) => family.trim()).filter(Boolean)))
    if (uniqueFamilies.length === 0) return
    const next: Record<string, boolean> = {}
    uniqueFamilies.forEach((family) => { next[fontKey(family)] = detectLocalFont(family) })
    setAvailability(next)
  }, [familyKey, families, refreshKey])

  return availability
}

/** 读取字体检测结果，undefined 表示尚未检测 */
function getFontAvailability(availability: Record<string, boolean>, family: string) {
  const key = fontKey(family)
  return key ? availability[key] : undefined
}

/** 九宫格字幕位置 */
const POSITION_OPTIONS: Array<{ value: SubtitlePosition; label: string }> = [
  { value: 'top_left', label: '左上' }, { value: 'top', label: '顶部' }, { value: 'top_right', label: '右上' },
  { value: 'middle_left', label: '左中' }, { value: 'center', label: '居中' }, { value: 'middle_right', label: '右中' },
  { value: 'bottom_left', label: '左下' }, { value: 'bottom', label: '底部' }, { value: 'bottom_right', label: '右下' },
]
const POSITION_GRID_OPTIONS: FieldOption[] = POSITION_OPTIONS.map((p) => [p.value, p.label])

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
  const [installingFontName, setInstallingFontName] = useState('')
  const [fontAvailabilityRefreshKey, setFontAvailabilityRefreshKey] = useState(0)
  const { addLog } = useTaskStore()

  const usesCustomLanguage = useMemo(() => !LANGUAGE_OPTIONS.some(([value]) => value === form.language), [form.language])
  const selectedLanguage = usesCustomLanguage ? 'custom' : form.language
  const selectedFont = useMemo(() => findFontPreset(form.font_name), [form.font_name])
  const fontFamiliesToCheck = useMemo(() => {
    const families = FONT_LIBRARY.flatMap((font) => [font.family, ...(font.aliases || [])])
    const currentFamily = form.font_name.trim()
    return currentFamily ? [...families, currentFamily] : families
  }, [form.font_name])
  const fontAvailability = useFontAvailability(fontFamiliesToCheck, fontAvailabilityRefreshKey)
  const currentFontAvailable = getFontAvailability(fontAvailability, form.font_name)

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

  const handleInstallFont = async (font: FontPreset) => {
    if (!font.installable) {
      addLog('warn', '这个字体没有内置下载源，请从字体官网安装后再选择。')
      return
    }
    setInstallingFontName(font.family)
    try {
      const result = await subtitleApi.installFont(font.family)
      updateForm('font_name', font.family)
      setFontAvailabilityRefreshKey((value) => value + 1)
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `安装字体失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setInstallingFontName('')
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">字幕设置</h2>
        <p className="text-sm text-muted-foreground">选择已保存预设后，可微调语言、字号、位置和颜色；右侧实时预览。</p>
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
                <FontPicker
                  value={form.font_name}
                  selectedFont={selectedFont}
                  availability={fontAvailability}
                  installingFontName={installingFontName}
                  onChange={(fontName) => updateForm('font_name', fontName)}
                  onInstallFont={handleInstallFont}
                />
                {currentFontAvailable === false && (
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs leading-5 text-warning">
                    <span className="min-w-0 flex-1">
                      当前电脑未检测到这个字体。{selectedFont?.installable ? '可以直接下载安装到当前用户字体目录。' : '请先从字体官网或系统字体册安装后再选择。'}
                    </span>
                    {selectedFont?.installable && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-8 border-warning/40 text-warning hover:bg-warning/15"
                        onClick={() => handleInstallFont(selectedFont)}
                        disabled={installingFontName === selectedFont.family}
                      >
                        {installingFontName === selectedFont.family ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Download className="mr-1.5 size-3.5" />}
                        {installingFontName === selectedFont.family ? '安装中' : '安装字体'}
                      </Button>
                    )}
                  </div>
                )}
                <p className="text-xs leading-5 text-muted-foreground">
                  免费字体可一键安装到当前用户字体目录；商业字体可以选择使用，但发布公开视频、商单或账号运营内容前需要自行确认授权。
                </p>
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
                <SelectField label="字幕识别方式" value={automationOptions.subtitle_recognition_mode} options={[['local', '本地识别（快·免费）'], ['gemini_full', 'Gemini 转写（最准·较慢·走文本API）'], ['gemini_align', 'Gemini 内容+本地时间轴']]} onChange={(v) => setAutomationOptions(saveAutomationPreferences({ subtitle_recognition_mode: v as typeof automationOptions.subtitle_recognition_mode }))} description="Gemini 模式识别更准但更慢，需先在「文本 API」配置 Gemini 渠道" />
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
            <PreviewBox form={form} languageLabel={selectedLanguage === 'custom' ? customLanguage || '自定义' : selectedLanguage} fontAvailable={currentFontAvailable} />
          </div>
        </aside>
      </div>
    </div>
  )
}

/** 字体授权标签 */
function FontLicenseBadge({ license }: { license: FontLicenseKind }) {
  const meta = FONT_LICENSE_META[license]
  return <Badge variant="outline" className={meta.className}>{meta.label}</Badge>
}

/** 字体本机状态标签 */
function FontAvailabilityBadge({ available }: { available?: boolean }) {
  if (available === undefined) return null
  return (
    <Badge
      variant="outline"
      className={available ? 'border-success/40 bg-success/10 text-success' : 'border-muted-foreground/30 bg-muted text-muted-foreground'}
    >
      {available ? '本机可用' : '未检测到'}
    </Badge>
  )
}

/** 判断当前字体是否命中字体库条目 */
function isSelectedFont(value: string, font: FontPreset) {
  const key = fontKey(value)
  return fontKey(font.family) === key || font.aliases?.some((alias) => fontKey(alias) === key) || false
}

/** 字体搜索匹配 */
function matchesFontQuery(font: FontPreset, query: string) {
  if (!query) return true
  const haystack = [font.name, font.family, font.note, ...(font.aliases || [])].join(' ').toLowerCase()
  return haystack.includes(query)
}

/** 字体弹出选择器 */
function FontPicker({ value, selectedFont, availability, installingFontName, onChange, onInstallFont }: {
  value: string
  selectedFont?: FontPreset
  availability: Record<string, boolean>
  installingFontName: string
  onChange: (fontName: string) => void
  onInstallFont: (font: FontPreset) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<FontFilterKind>('all')
  const normalizedQuery = query.trim().toLowerCase()
  const selectedAvailability = getFontAvailability(availability, value)
  const filteredFonts = useMemo(
    () => FONT_LIBRARY.filter((font) => (filter === 'all' || font.license === filter) && matchesFontQuery(font, normalizedQuery)),
    [filter, normalizedQuery],
  )

  const handleSelect = (font: FontPreset) => {
    onChange(font.family)
    setOpen(false)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-muted-foreground">字体库</span>
        <div className="flex shrink-0 items-center gap-1.5">
          {selectedFont ? <FontLicenseBadge license={selectedFont.license} /> : <Badge variant="outline">自定义</Badge>}
          <FontAvailabilityBadge available={selectedAvailability} />
        </div>
      </div>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" className="h-auto min-h-12 w-full justify-between px-3 py-2 text-left">
            <span className="flex min-w-0 items-center gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Type className="size-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium" style={{ fontFamily: buildCssFontFamily(value) }}>
                  {selectedFont?.name || value || '选择字体'}
                </span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">{value || '未设置字体名称'}</span>
              </span>
            </span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[620px] max-w-[calc(100vw-2rem)] p-0">
          <div className="space-y-3 border-b p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索字体名、别名或厂商"
                className="pl-8"
              />
            </div>
            <SegmentedField value={filter} options={FONT_FILTER_OPTIONS} onChange={(nextFilter) => setFilter(nextFilter as FontFilterKind)} />
          </div>

          <div className="max-h-[360px] overflow-y-auto p-2">
            {filteredFonts.length > 0 ? (
              <div className="grid gap-2">
                {filteredFonts.map((font) => (
                  <FontPickerOption
                    key={`${font.license}-${font.family}`}
                    font={font}
                    selected={isSelectedFont(value, font)}
                    available={getFontAvailability(availability, font.family)}
                    installing={installingFontName === font.family}
                    onSelect={() => handleSelect(font)}
                    onInstall={() => onInstallFont(font)}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-dashed p-5 text-center text-sm text-muted-foreground">
                没有匹配的字体，请换关键词或筛选条件。
              </div>
            )}
          </div>

          <div className="border-t px-3 py-2 text-xs leading-5 text-muted-foreground">
            免费字体可直接安装；商业字体不会被禁用，但需要你自行确认授权并先安装到系统。
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

/** 字体选择弹层条目 */
function FontPickerOption({ font, selected, available, installing, onSelect, onInstall }: {
  font: FontPreset
  selected: boolean
  available?: boolean
  installing: boolean
  onSelect: () => void
  onInstall: () => void
}) {
  const canInstall = font.installable && available === false
  return (
    <div
      style={{ fontFamily: buildCssFontFamily(font.family) }}
      className={cn(
        'rounded-lg border bg-card p-3 text-xs transition-colors',
        selected ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left hover:text-foreground">
          <span className="block truncate text-sm font-medium">{font.name}</span>
          <span className="mt-0.5 block truncate font-mono text-[11px] opacity-75">
            {font.family}{font.aliases?.length ? ` / ${font.aliases[0]}` : ''}
          </span>
          <span className="mt-2 line-clamp-2 block leading-snug opacity-80">{font.note}</span>
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {selected ? <Check className="size-4 shrink-0" /> : <FontLicenseBadge license={font.license} />}
          <FontAvailabilityBadge available={available} />
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <Button type="button" size="sm" variant={selected ? 'secondary' : 'outline'} className="h-8 px-3" onClick={onSelect}>
          {selected ? '已选择' : '选择'}
        </Button>
        {canInstall ? (
          <Button type="button" size="sm" variant="outline" className="h-8 px-3" onClick={onInstall} disabled={installing}>
            {installing ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Download className="mr-1.5 size-3.5" />}
            {installing ? '安装中' : '安装字体'}
          </Button>
        ) : (
          <span className="text-[11px] text-muted-foreground">
            {available === true ? '已可用' : available === false && !font.installable ? '需自行安装' : font.installable ? '可自动安装' : '按系统授权'}
          </span>
        )}
      </div>
    </div>
  )
}

/** 预览框 */
function PreviewBox({ form, languageLabel, fontAvailable }: { form: SubtitlePresetForm; languageLabel: string; fontAvailable?: boolean }) {
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
          <div className="max-w-full rounded px-3 py-2 text-center leading-tight" style={{ background: previewBackground, color: form.font_color, fontFamily: buildCssFontFamily(form.font_name), fontSize: `${previewFontSize}px`, textShadow: previewTextShadow, lineHeight: 1.18 }}>
            <div className="break-words">主字幕预览文本</div>
            {form.line_mode === 'double' && <div className="mt-1 break-words" style={{ color: form.secondary_color, fontSize: `${secondaryPreviewFontSize}px` }}>Second subtitle line</div>}
          </div>
        </div>
      </div>
      {fontAvailable === false && (
        <div className="rounded-md border border-warning/30 bg-warning/10 p-2 text-xs leading-5 text-warning">
          未检测到当前字体，预览已使用备用字体显示；导出前请确认本机已安装该字体。
        </div>
      )}
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
