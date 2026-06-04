// src/features/effects/EffectsPanel.tsx
// 画面处理面板 - 配置视频增强、差异化处理和一键自动流程复用参数
// 交互重做：快捷预设卡片 + 常用项露出 + 专业参数收进高级折叠（简化优先）

import { useEffect, useMemo, useState } from 'react'
import { effectsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import type { ProcessingConfig, ProcessingPreset, RandomRange } from '@/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { SelectField, SwitchField, RangeField, SegmentedField, NumberField, TextField, type FieldOption } from '@/components/fields'

/** 创建随机范围配置 */
const range = (min: number, max: number, value: number | null = null, random = true): RandomRange => ({
  enabled: true,
  random,
  value,
  min,
  max,
})

/** 自动化参数本地缓存键 */
export const AUTOMATION_CONFIG_STORAGE_KEY = 'youtube-video-processor:auto-config'

/** 画面处理配置版本，用于把旧的重 CPU 默认参数迁移成极速 1080p 方案 */
const PROCESSING_CONFIG_VERSION = 3

/** 默认画面处理配置 */
export const createDefaultProcessingConfig = (): ProcessingConfig => ({
  version: PROCESSING_CONFIG_VERSION,
  adjustments: {
    enabled: false,
    brightness: range(0, 0.1),
    contrast: range(1, 1.2),
    saturation: range(1, 1.1),
    sharpness: { ...range(0, 0, 0, false), enabled: false },
    denoise: { ...range(0, 0, 0, false), enabled: false },
  },
  canvas: {
    enabled: true,
    resolution: '1080p',
    mode: 'keep',
    width: 1920,
    height: 1080,
    background_enabled: false,
    reflection_enabled: false,
    grid_enabled: false,
  },
  transform: {
    enabled: true,
    rotate_mode: 'none',
    flip_horizontal: true,
    flip_vertical: false,
    random_rotate: { ...range(0, 0, 0, false), enabled: false },
    remove_black_bars: false,
    show_full_frame: true,
  },
  timing: {
    enabled: false,
    fps: range(30, 30, 30, false),
    drop_frame: {
      enabled: false,
      interval: range(25, 30),
    },
    dynamic_zoom: {
      ...range(0.01, 0.02),
      enabled: false,
    },
  },
  bitrate: {
    enabled: true,
    mode: 'fixed',
    fixed_kbps: range(2200, 2200, 2200, false),
    multiplier: range(1.05, 1.95),
    quality_mode: 'size',
  },
  acceleration: {
    enabled: true,
    mode: 'auto',
    quality: 'size',
  },
})

/** 轻度处理模板 */
function createLightProcessingConfig(): ProcessingConfig {
  const config = createDefaultProcessingConfig()
  config.adjustments.enabled = true
  config.adjustments.brightness = range(0, 0.04)
  config.adjustments.contrast = range(1, 1.08)
  config.adjustments.saturation = range(1, 1.06)
  config.adjustments.sharpness = range(0.3, 0.7)
  config.adjustments.denoise = range(0, 1)
  config.transform.flip_horizontal = false
  config.transform.random_rotate.enabled = false
  config.bitrate.fixed_kbps = range(3500, 3500, 3500, false)
  return config
}

/** 强处理模板 */
function createStrongProcessingConfig(): ProcessingConfig {
  const config = createDefaultProcessingConfig()
  config.adjustments.enabled = true
  config.adjustments.brightness = range(0.02, 0.12)
  config.adjustments.contrast = range(1.08, 1.25)
  config.adjustments.saturation = range(1.08, 1.22)
  config.adjustments.sharpness = range(1.1, 1.8)
  config.adjustments.denoise = range(1.5, 3)
  config.canvas.resolution = '1080p'
  config.canvas.mode = 'crop'
  config.transform.random_rotate = range(-1.5, 1.5)
  config.bitrate.fixed_kbps = range(3500, 3500, 3500, false)
  return config
}

/** 清晰优先模板 */
function createQualityProcessingConfig(): ProcessingConfig {
  const config = createDefaultProcessingConfig()
  config.adjustments.enabled = true
  config.canvas.resolution = '1080p'
  config.canvas.mode = 'keep'
  config.adjustments.sharpness = range(0.8, 1.2)
  config.adjustments.denoise = range(0.8, 1.5)
  config.transform.flip_horizontal = false
  config.transform.random_rotate.enabled = false
  config.bitrate.quality_mode = 'quality'
  config.bitrate.fixed_kbps = range(4200, 4200, 4200, false)
  return config
}

/** 快速处理模板 */
const QUICK_TEMPLATES = [
  { id: 'light', name: '轻度', description: '低干扰，保留原片观感', factory: createLightProcessingConfig },
  { id: 'standard', name: '极速', description: '速度优先，仍输出 1080p', factory: createDefaultProcessingConfig },
  { id: 'strong', name: '强处理', description: '画面差异更明显', factory: createStrongProcessingConfig },
  { id: 'quality', name: '清晰优先', description: '1080p 和较高码率', factory: createQualityProcessingConfig },
]

/** 判断是否已经保存过自动化参数 */
export function hasStoredAutomationConfig() {
  return typeof localStorage !== 'undefined' && Boolean(localStorage.getItem(AUTOMATION_CONFIG_STORAGE_KEY))
}

/** 读取自动化参数，供一键流程复用 */
export function loadAutomationConfig(): ProcessingConfig {
  if (typeof localStorage === 'undefined') {
    return createDefaultProcessingConfig()
  }

  try {
    const saved = localStorage.getItem(AUTOMATION_CONFIG_STORAGE_KEY)
    if (!saved) return createDefaultProcessingConfig()
    return normalizeProcessingConfig(JSON.parse(saved))
  } catch {
    return createDefaultProcessingConfig()
  }
}

/** 迁移旧缓存，避免继续使用锐化、降噪、随机旋转和强制 fps 这类重 CPU 默认项 */
function normalizeProcessingConfig(value: ProcessingConfig): ProcessingConfig {
  if (value.version === PROCESSING_CONFIG_VERSION) {
    return value
  }
  const next = createDefaultProcessingConfig()
  return {
    ...next,
    ...value,
    version: PROCESSING_CONFIG_VERSION,
    adjustments: {
      ...next.adjustments,
      ...(value.adjustments || {}),
      enabled: false,
      sharpness: next.adjustments.sharpness,
      denoise: next.adjustments.denoise,
    },
    transform: {
      ...next.transform,
      ...(value.transform || {}),
      random_rotate: next.transform.random_rotate,
    },
    canvas: {
      ...next.canvas,
      ...(value.canvas || {}),
      resolution: '1080p',
      width: 1920,
      height: 1080,
    },
    bitrate: {
      ...next.bitrate,
      ...(value.bitrate || {}),
      mode: 'fixed',
      fixed_kbps: next.bitrate.fixed_kbps,
      quality_mode: 'size',
    },
    timing: {
      ...next.timing,
      ...(value.timing || {}),
      enabled: false,
      drop_frame: value.timing?.drop_frame || next.timing.drop_frame,
      dynamic_zoom: value.timing?.dynamic_zoom || next.timing.dynamic_zoom,
    },
    acceleration: {
      ...next.acceleration,
      ...(value.acceleration || {}),
      enabled: true,
      mode: value.acceleration?.mode || 'auto',
      quality: 'size',
    },
  }
}

/** 保存自动化参数，确保设置和一键流程一致 */
export function saveAutomationConfig(config: ProcessingConfig) {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(AUTOMATION_CONFIG_STORAGE_KEY, JSON.stringify(config))
  }
}

/** 下拉选项常量 */
const RESOLUTION_OPTIONS: FieldOption[] = [['720p', '720p'], ['1080p', '1080p'], ['original', '原始分辨率'], ['custom', '自定义']]
const CANVAS_MODE_OPTIONS: FieldOption[] = [['keep', '保持比例（留黑边）'], ['stretch', '拉伸填满'], ['crop', '裁切填满'], ['blur_background', '模糊背景填充']]
const ACCEL_MODE_OPTIONS: FieldOption[] = [['auto', '自动选择'], ['cpu', 'CPU（兼容）'], ['nvidia', 'NVIDIA'], ['intel', 'Intel'], ['amd', 'AMD']]
const QUALITY_OPTIONS: FieldOption[] = [['size', '体积优先'], ['balanced', '均衡'], ['quality', '清晰优先']]
const ROTATE_OPTIONS: FieldOption[] = [['none', '不旋转'], ['left90', '左转 90°'], ['right90', '右转 90°']]
const BITRATE_MODE_OPTIONS: FieldOption[] = [['fixed', '固定码率'], ['multiplier', '按倍率']]

/**
 * 画面处理面板
 */
export function EffectsPanel() {
  return <EffectsSettingsPanel variant="page" />
}

/** 自动化设置面板属性 */
interface EffectsSettingsPanelProps {
  /** 兼容旧调用，现统一为响应式布局 */
  variant?: 'page' | 'compact'
  /** 外部触发完成时调用（设置区为独立工作区时不使用） */
  onClose?: () => void
}

/**
 * 画面处理设置面板
 * 设置好参数后会被一键完成流程自动复用，也可对单个视频预览/试处理。
 */
export function EffectsSettingsPanel(_props: EffectsSettingsPanelProps = {}) {
  const [presets, setPresets] = useState<ProcessingPreset[]>([])
  const [config, setConfig] = useState<ProcessingConfig>(() => loadAutomationConfig())
  const [presetName, setPresetName] = useState('自定义预设')
  const [videoPath, setVideoPath] = useState('')
  const [filterGraph, setFilterGraph] = useState('')
  const [isBusy, setIsBusy] = useState(false)
  const { tasks, addTask, addLog } = useTaskStore()

  const latestVideoPath = useMemo(() => {
    const completed = tasks.find((task) => task.status === 'completed' && task.output_path)
    return completed?.output_path || ''
  }, [tasks])

  /** 加载后端保存的处理预设 */
  const loadPresets = async () => {
    try {
      const data = await effectsApi.listPresets()
      setPresets(data)
      const defaultPreset = data.find((item) => item.is_default) || data[0]
      if (defaultPreset && !hasStoredAutomationConfig()) {
        setConfig(defaultPreset.config)
        setPresetName(defaultPreset.name)
      }
    } catch (error) {
      addLog('error', `加载画面处理预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => { loadPresets() }, [])
  useEffect(() => { saveAutomationConfig(config) }, [config])
  useEffect(() => { if (!videoPath && latestVideoPath) setVideoPath(latestVideoPath) }, [latestVideoPath, videoPath])

  /** 更新随机范围字段 */
  const updateRange = (path: string[], updates: Partial<RandomRange>) => {
    setConfig((current) => {
      const next = structuredClone(current)
      let target: any = next
      for (const key of path) target = target[key]
      Object.assign(target, updates)
      return next
    })
  }

  /** 更新普通配置字段 */
  const updateValue = (path: string[], value: unknown) => {
    setConfig((current) => {
      const next = structuredClone(current)
      let target: any = next
      for (let index = 0; index < path.length - 1; index++) target = target[path[index]]
      target[path[path.length - 1]] = value
      return next
    })
  }

  /** 应用快速模板 */
  const applyQuickTemplate = (template: (typeof QUICK_TEMPLATES)[number]) => {
    setConfig(template.factory())
    setPresetName(`${template.name}处理`)
    addLog('info', `已应用画面处理模板: ${template.name}`)
  }

  /** 生成 ffmpeg 滤镜预览 */
  const handleBuildFilter = async () => {
    try {
      const result = await effectsApi.buildFilterGraph(config)
      setFilterGraph(result.filter_graph)
      addLog('info', '已生成 ffmpeg 滤镜参数')
    } catch (error) {
      addLog('error', `生成滤镜失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 保存当前处理预设 */
  const handleSavePreset = async () => {
    if (!presetName.trim()) { addLog('warn', '请输入画面处理预设名称'); return }
    try {
      const saved = await effectsApi.createPreset({ name: presetName, intensity: 'custom', config })
      addLog('info', `画面处理预设已保存: ${saved.name}`)
      loadPresets()
    } catch (error) {
      addLog('error', `保存画面处理预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 应用已有预设 */
  const handleSelectPreset = (presetId: string) => {
    const preset = presets.find((item) => item.id === Number(presetId))
    if (!preset) return
    setConfig(preset.config)
    setPresetName(preset.name)
    addLog('info', `已切换画面处理预设: ${preset.name}`)
  }

  /** 生成短片段预览 */
  const handlePreview = async () => {
    if (!videoPath.trim()) { addLog('warn', '请先填写已下载的视频路径'); return }
    setIsBusy(true)
    try {
      const result = await effectsApi.preview({ video_path: videoPath, preset: config })
      setFilterGraph(result.filter_graph)
      addLog('info', `预览片段已生成: ${result.output_path}`)
    } catch (error) {
      addLog('error', `生成预览失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsBusy(false)
    }
  }

  /** 执行完整画面处理 */
  const handleApply = async () => {
    if (!videoPath.trim()) { addLog('warn', '请先填写已下载的视频路径'); return }
    setIsBusy(true)
    try {
      const result = await effectsApi.apply({ video_path: videoPath, preset: config })
      setFilterGraph(result.filter_graph)
      if (result.task_id) {
        addTask({
          id: result.task_id, video_id: 0, task_type: 'effects', status: 'completed', progress: 100,
          output_path: result.output_path, error_message: null,
          created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
        })
      }
      addLog('info', `画面处理完成: ${result.output_path}`)
    } catch (error) {
      addLog('error', `画面处理失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">画面处理</h2>
        <p className="text-sm text-muted-foreground">这里只处理差异化、画布和处理码率。最终导出的格式、分辨率和成品码率请到单独的“最终导出”设置里调整。</p>
      </div>

      {/* 快捷预设卡片 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {QUICK_TEMPLATES.map((template) => (
          <button
            key={template.id}
            onClick={() => applyQuickTemplate(template)}
            className="rounded-lg border bg-card p-3 text-left transition-colors hover:border-primary hover:bg-primary/5"
          >
            <p className="text-sm font-medium">{template.name}</p>
            <p className="mt-1 text-xs text-muted-foreground">{template.description}</p>
          </button>
        ))}
      </div>

      {/* 常用 */}
      <Card>
        <CardHeader><CardTitle className="text-sm">常用</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <SelectField label="处理画布分辨率" value={config.canvas.resolution} options={RESOLUTION_OPTIONS} onChange={(v) => updateValue(['canvas', 'resolution'], v)} />
          <SelectField label="硬件加速" value={config.acceleration?.mode ?? 'auto'} options={ACCEL_MODE_OPTIONS} onChange={(v) => updateValue(['acceleration', 'mode'], v)} description="自动选择可用 GPU，失败会回退 CPU" />
          {config.canvas.resolution === 'custom' && (
            <>
              <NumberField label="宽度" value={config.canvas.width} onChange={(v) => updateValue(['canvas', 'width'], v)} suffix="px" />
              <NumberField label="高度" value={config.canvas.height} onChange={(v) => updateValue(['canvas', 'height'], v)} suffix="px" />
            </>
          )}
        </CardContent>
      </Card>

      {/* 高级折叠 */}
      <Accordion type="multiple" className="space-y-2">
        <AccordionItem value="adjust" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">画面微调（亮度 / 对比度 / 锐化等）</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SwitchField label="启用画面微调" checked={config.adjustments.enabled} onChange={(v) => updateValue(['adjustments', 'enabled'], v)} />
            <RangeField label="亮度" value={config.adjustments.brightness} min={-0.3} max={0.3} step={0.01} onChange={(u) => updateRange(['adjustments', 'brightness'], u)} />
            <RangeField label="对比度" value={config.adjustments.contrast} min={0.5} max={2} step={0.01} onChange={(u) => updateRange(['adjustments', 'contrast'], u)} />
            <RangeField label="饱和度" value={config.adjustments.saturation} min={0} max={2} step={0.01} onChange={(u) => updateRange(['adjustments', 'saturation'], u)} />
            <RangeField label="锐化" value={config.adjustments.sharpness} min={0} max={5} step={0.1} decimals={1} onChange={(u) => updateRange(['adjustments', 'sharpness'], u)} />
            <RangeField label="降噪" value={config.adjustments.denoise} min={0} max={10} step={0.1} decimals={1} onChange={(u) => updateRange(['adjustments', 'denoise'], u)} />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="canvas" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">画布与裁切</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SwitchField label="启用画布处理" checked={config.canvas.enabled} onChange={(v) => updateValue(['canvas', 'enabled'], v)} />
            <SelectField label="缩放模式" value={config.canvas.mode} options={CANVAS_MODE_OPTIONS} onChange={(v) => updateValue(['canvas', 'mode'], v)} description="裁切填满会轻微放大、改变构图，也利于差异化" />
            <SelectField label="加速质量" value={config.acceleration?.quality ?? 'size'} options={QUALITY_OPTIONS} onChange={(v) => updateValue(['acceleration', 'quality'], v)} />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="motion" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">运动与帧率</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <SwitchField label="启用翻转 / 旋转" checked={config.transform.enabled} onChange={(v) => updateValue(['transform', 'enabled'], v)} />
            <SegmentedField label="旋转" value={config.transform.rotate_mode} options={ROTATE_OPTIONS} onChange={(v) => updateValue(['transform', 'rotate_mode'], v)} />
            <div className="grid grid-cols-2 gap-2">
              <SwitchField label="水平翻转" checked={config.transform.flip_horizontal} onChange={(v) => updateValue(['transform', 'flip_horizontal'], v)} />
              <SwitchField label="垂直翻转" checked={config.transform.flip_vertical} onChange={(v) => updateValue(['transform', 'flip_vertical'], v)} />
            </div>
            <RangeField label="随机轻微旋转" value={config.transform.random_rotate} min={-3} max={3} step={0.1} decimals={1} suffix="°" description="每个视频随机旋转一点角度，增强差异" onChange={(u) => updateRange(['transform', 'random_rotate'], u)} />
            <SwitchField label="启用帧率 / 抽帧" checked={config.timing.enabled} onChange={(v) => updateValue(['timing', 'enabled'], v)} />
            <RangeField label="帧率" value={config.timing.fps} min={1} max={60} step={1} decimals={0} suffix=" fps" onChange={(u) => updateRange(['timing', 'fps'], u)} />
            <SwitchField label="抽帧" description="每隔若干帧丢一帧，改变节奏" checked={config.timing.drop_frame.enabled} onChange={(v) => updateValue(['timing', 'drop_frame', 'enabled'], v)} />
            <RangeField label="每 N 帧抽一帧" value={config.timing.drop_frame.interval} min={2} max={60} step={1} decimals={0} onChange={(u) => updateRange(['timing', 'drop_frame', 'interval'], u)} />
            <RangeField label="动态缩放" value={config.timing.dynamic_zoom} min={0} max={0.3} step={0.01} description="画面缓慢放大，幅度很小" onChange={(u) => updateRange(['timing', 'dynamic_zoom'], u)} />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="output" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">码率与处理输出</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <p className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
              这里的码率只影响画面处理阶段。最终成品的导出格式、分辨率和码率，请到“最终导出”里单独设置。
            </p>
            <SwitchField label="启用码率控制" checked={config.bitrate.enabled} onChange={(v) => updateValue(['bitrate', 'enabled'], v)} />
            <SegmentedField label="码率方式" value={config.bitrate.mode} options={BITRATE_MODE_OPTIONS} onChange={(v) => updateValue(['bitrate', 'mode'], v)} />
            {config.bitrate.mode === 'fixed' ? (
              <RangeField label="固定码率" value={config.bitrate.fixed_kbps} min={500} max={8000} step={100} decimals={0} suffix=" kb/s" onChange={(u) => updateRange(['bitrate', 'fixed_kbps'], u)} />
            ) : (
              <RangeField label="码率倍率" value={config.bitrate.multiplier} min={0.5} max={3} step={0.05} description="相对源码率的倍数" onChange={(u) => updateRange(['bitrate', 'multiplier'], u)} />
            )}
            <SelectField label="质量取向" value={config.bitrate.quality_mode} options={QUALITY_OPTIONS} onChange={(v) => updateValue(['bitrate', 'quality_mode'], v)} />
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="test" className="rounded-lg border px-4">
          <AccordionTrigger className="text-sm">预览与测试（对单个视频试用）</AccordionTrigger>
          <AccordionContent className="space-y-3 pb-3">
            <TextField label="视频路径" value={videoPath} placeholder="下载完成后自动填入，也可粘贴本地视频路径" onChange={setVideoPath} />
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={handleBuildFilter}>生成参数</Button>
              <Button variant="outline" size="sm" disabled={isBusy} onClick={handlePreview}>生成预览</Button>
              <Button size="sm" disabled={isBusy} onClick={handleApply}>{isBusy ? '处理中…' : '开始处理'}</Button>
            </div>
            {filterGraph && (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs text-muted-foreground select-text">{filterGraph}</pre>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {/* 预设保存 */}
      <Card>
        <CardHeader><CardTitle className="text-sm">预设</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {presets.length > 0 && (
            <SelectField label="加载已保存预设" value="" placeholder="选择一个预设载入" options={presets.map((p) => [String(p.id), p.name] as FieldOption)} onChange={handleSelectPreset} />
          )}
          <div className="flex flex-wrap items-end gap-2">
            <TextField label="预设名称" value={presetName} onChange={setPresetName} className="min-w-44 flex-1" />
            <Button variant="outline" onClick={() => setConfig(createDefaultProcessingConfig())}>重置</Button>
            <Button onClick={handleSavePreset}>保存预设</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
