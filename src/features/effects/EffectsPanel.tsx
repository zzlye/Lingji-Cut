// src/features/effects/EffectsPanel.tsx
// 画面处理面板 - 配置视频增强、差异化处理和一键自动流程复用参数

import { useEffect, useMemo, useState } from 'react'
import { effectsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import type { ProcessingConfig, ProcessingPreset, RandomRange } from '@/types'

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

/** 保存自动化参数，确保齿轮设置和一键流程一致 */
export function saveAutomationConfig(config: ProcessingConfig) {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(AUTOMATION_CONFIG_STORAGE_KEY, JSON.stringify(config))
  }
}

/**
 * 画面处理面板
 * 提供桌面工具式的画面差异化参数配置。
 */
export function EffectsPanel() {
  return <EffectsSettingsPanel variant="page" />
}

/** 自动化设置面板属性 */
interface EffectsSettingsPanelProps {
  /** page 用于主页面，compact 用于顶部齿轮弹层 */
  variant?: 'page' | 'compact'
  /** 外部触发完成时调用 */
  onClose?: () => void
}

/** 画面处理内部分类 */
type EffectsSection = 'setup' | 'adjustments' | 'canvas' | 'motion' | 'output'

/** 分组导航配置 */
const EFFECTS_SECTIONS: Array<{ id: EffectsSection; label: string; description: string }> = [
  { id: 'setup', label: '流程', description: '预设、视频路径和执行' },
  { id: 'adjustments', label: '画面微调', description: '亮度、对比度、锐化' },
  { id: 'canvas', label: '画布', description: '分辨率、裁切和背景' },
  { id: 'motion', label: '运动', description: '翻转、旋转、帧率' },
  { id: 'output', label: '输出', description: '码率、体积和参数' },
]

/**
 * 自动化画面处理设置面板
 * 设置好参数后可保存模板、生成参数、预览，并由顶部一键流程复用。
 */
export function EffectsSettingsPanel({ variant = 'page', onClose }: EffectsSettingsPanelProps) {
  const [presets, setPresets] = useState<ProcessingPreset[]>([])
  const [config, setConfig] = useState<ProcessingConfig>(() => loadAutomationConfig())
  const [presetName, setPresetName] = useState('标准处理')
  const [videoPath, setVideoPath] = useState('')
  const [filterGraph, setFilterGraph] = useState('')
  const [isBusy, setIsBusy] = useState(false)
  const [activeSection, setActiveSection] = useState<EffectsSection>('setup')
  const { tasks, addTask, addLog } = useTaskStore()

  const isCompact = variant === 'compact'
  const activeMeta = EFFECTS_SECTIONS.find((section) => section.id === activeSection) || EFFECTS_SECTIONS[0]
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

  useEffect(() => {
    loadPresets()
  }, [])

  useEffect(() => {
    saveAutomationConfig(config)
  }, [config])

  useEffect(() => {
    if (!videoPath && latestVideoPath) {
      setVideoPath(latestVideoPath)
    }
  }, [latestVideoPath, videoPath])

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
      for (let index = 0; index < path.length - 1; index++) {
        target = target[path[index]]
      }
      target[path[path.length - 1]] = value
      return next
    })
  }

  /** 应用快速模板 */
  const applyQuickTemplate = (template: (typeof QUICK_TEMPLATES)[number]) => {
    const nextConfig = template.factory()
    setConfig(nextConfig)
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
    if (!presetName.trim()) {
      addLog('warn', '请输入画面处理预设名称')
      return
    }
    try {
      const saved = await effectsApi.createPreset({
        name: presetName,
        intensity: 'custom',
        config,
      })
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
    if (!videoPath.trim()) {
      addLog('warn', '请先填写已下载的视频路径')
      return
    }
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
    if (!videoPath.trim()) {
      addLog('warn', '请先填写已下载的视频路径')
      return
    }
    setIsBusy(true)
    try {
      const result = await effectsApi.apply({ video_path: videoPath, preset: config })
      setFilterGraph(result.filter_graph)
      if (result.task_id) {
        addTask({
          id: result.task_id,
          video_id: 0,
          task_type: 'effects',
          status: 'completed',
          progress: 100,
          output_path: result.output_path,
          error_message: null,
          created_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
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
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-medium">画面处理</h3>
            <p className="truncate text-xs text-foreground-muted">差异化、画布和输出参数会被一键完成流程自动复用。</p>
          </div>
          {onClose && (
            <button onClick={onClose} className="h-9 w-9 rounded-md border border-border text-sm hover:bg-white/5" title="关闭设置" aria-label="关闭设置">
              <svg className="mx-auto h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className={`grid gap-4 ${isCompact ? 'grid-cols-[150px_minmax(0,1fr)] max-lg:grid-cols-1' : 'grid-cols-[180px_minmax(0,1fr)_280px] max-xl:grid-cols-[170px_minmax(0,1fr)] max-lg:grid-cols-1'}`}>
          <aside className="space-y-3">
            <section className="rounded-lg border border-border bg-background p-3">
              <div className="mb-3">
                <h4 className="text-sm font-medium">处理阶段</h4>
                <p className="text-xs text-foreground-muted">按步骤配置，不挤成一排。</p>
              </div>
              <nav className="space-y-1">
                {EFFECTS_SECTIONS.map((section) => (
                  <button
                    key={section.id}
                    onClick={() => setActiveSection(section.id)}
                    className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
                      activeSection === section.id
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-transparent text-foreground-muted hover:border-border hover:bg-white/5 hover:text-foreground'
                    }`}
                  >
                    <div className="text-sm font-medium">{section.label}</div>
                    <div className="mt-0.5 text-[10px] opacity-80">{section.description}</div>
                  </button>
                ))}
              </nav>
            </section>

            <section className="rounded-lg border border-border bg-background p-3">
              <h4 className="text-sm font-medium">快捷模板</h4>
              <div className="mt-3 space-y-2">
                {QUICK_TEMPLATES.map((template) => (
                  <button
                    key={template.id}
                    onClick={() => applyQuickTemplate(template)}
                    className="w-full rounded-md border border-border bg-background-elevated p-2 text-left transition-colors hover:border-border-bright hover:bg-white/5"
                  >
                    <div className="text-xs font-medium">{template.name}</div>
                    <div className="mt-0.5 text-[10px] text-foreground-muted">{template.description}</div>
                  </button>
                ))}
              </div>
            </section>
          </aside>

          <main className="min-w-0 space-y-4">
            <section className="rounded-lg border border-border bg-background p-4">
              <div className="flex flex-wrap items-end gap-3">
                <SelectField
                  label="已保存预设"
                  value=""
                  options={[['', '选择预设'], ...presets.map((preset) => [String(preset.id), preset.name] as [string, string])]}
                  onChange={handleSelectPreset}
                  className="min-w-44 flex-1"
                />
                <TextField label="预设名称" value={presetName} onChange={setPresetName} className="min-w-44 flex-1" />
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => setConfig(createDefaultProcessingConfig())} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5">
                    重置
                  </button>
                  <button onClick={handleSavePreset} className="h-9 rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground hover:bg-accent/90">
                    保存模板
                  </button>
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title={activeMeta.label} description={activeMeta.description} />
              <div className="mt-4">
                {activeSection === 'setup' && (
                  <SetupSection
                    videoPath={videoPath}
                    filterGraph={filterGraph}
                    isBusy={isBusy}
                    onVideoPathChange={setVideoPath}
                    onBuildFilter={handleBuildFilter}
                    onPreview={handlePreview}
                    onApply={handleApply}
                  />
                )}
                {activeSection === 'adjustments' && (
                  <AdjustmentsSection
                    config={config}
                    updateValue={updateValue}
                    updateRange={updateRange}
                  />
                )}
                {activeSection === 'canvas' && (
                  <CanvasSection
                    config={config}
                    updateValue={updateValue}
                  />
                )}
                {activeSection === 'motion' && (
                  <MotionSection
                    config={config}
                    updateValue={updateValue}
                    updateRange={updateRange}
                  />
                )}
                {activeSection === 'output' && (
                  <OutputSection
                    config={config}
                    filterGraph={filterGraph}
                    updateValue={updateValue}
                    updateRange={updateRange}
                  />
                )}
              </div>
            </section>
          </main>

          {!isCompact && (
            <aside className="space-y-4 max-xl:col-start-2 max-lg:col-start-auto">
              <SummaryPanel config={config} filterGraph={filterGraph} />
            </aside>
          )}
        </div>
      </div>
    </div>
  )
}

/** 流程配置 */
function SetupSection({ videoPath, filterGraph, isBusy, onVideoPathChange, onBuildFilter, onPreview, onApply }: {
  videoPath: string
  filterGraph: string
  isBusy: boolean
  onVideoPathChange: (value: string) => void
  onBuildFilter: () => void
  onPreview: () => void
  onApply: () => void
}) {
  return (
    <div className="space-y-4">
      <TextField
        label="视频路径"
        value={videoPath}
        placeholder="下载完成后自动填入，也可以粘贴本地视频路径"
        onChange={onVideoPathChange}
      />
      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        <button onClick={onBuildFilter} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5">
          生成参数
        </button>
        <button onClick={onPreview} disabled={isBusy} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50">
          生成预览
        </button>
        <button onClick={onApply} disabled={isBusy} className="h-9 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {isBusy ? '处理中...' : '开始处理'}
        </button>
      </div>
      {filterGraph && (
        <CodeBlock title="ffmpeg 滤镜参数" value={filterGraph} />
      )}
    </div>
  )
}

/** 画面调整配置 */
function AdjustmentsSection({ config, updateValue, updateRange }: SectionProps) {
  return (
    <TogglePanel
      title="画面微调"
      description="对每个视频做轻微随机变化，避免画面完全一致。"
      enabled={config.adjustments.enabled}
      onToggle={(value) => updateValue(['adjustments', 'enabled'], value)}
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
        <RangeField label="亮度" value={config.adjustments.brightness} onChange={(updates) => updateRange(['adjustments', 'brightness'], updates)} />
        <RangeField label="对比度" value={config.adjustments.contrast} onChange={(updates) => updateRange(['adjustments', 'contrast'], updates)} />
        <RangeField label="饱和度" value={config.adjustments.saturation} onChange={(updates) => updateRange(['adjustments', 'saturation'], updates)} />
        <RangeField label="锐化" value={config.adjustments.sharpness} onChange={(updates) => updateRange(['adjustments', 'sharpness'], updates)} />
        <RangeField label="降噪" value={config.adjustments.denoise} onChange={(updates) => updateRange(['adjustments', 'denoise'], updates)} />
      </div>
    </TogglePanel>
  )
}

/** 画布配置 */
function CanvasSection({ config, updateValue }: Pick<SectionProps, 'config' | 'updateValue'>) {
  return (
    <TogglePanel
      title="分辨率与画布"
      description="控制导出尺寸、裁切方式和背景填充。"
      enabled={config.canvas.enabled}
      onToggle={(value) => updateValue(['canvas', 'enabled'], value)}
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
        <SelectField label="分辨率" value={config.canvas.resolution} options={[['720p', '720p'], ['1080p', '1080p'], ['original', '原分辨率'], ['custom', '自定义']]} onChange={(value) => updateValue(['canvas', 'resolution'], value)} />
        <SelectField label="画布模式" value={config.canvas.mode} options={[['keep', '原比例'], ['stretch', '拉伸'], ['crop', '裁切'], ['blur_background', '背景模糊']]} onChange={(value) => updateValue(['canvas', 'mode'], value)} />
        <NumberField label="宽度" value={config.canvas.width} onChange={(value) => updateValue(['canvas', 'width'], value)} />
        <NumberField label="高度" value={config.canvas.height} onChange={(value) => updateValue(['canvas', 'height'], value)} />
      </div>
      <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
        <CheckboxCard label="背景填充" checked={config.canvas.background_enabled} onChange={(value) => updateValue(['canvas', 'background_enabled'], value)} />
        <CheckboxCard label="背景倒影" checked={config.canvas.reflection_enabled} onChange={(value) => updateValue(['canvas', 'reflection_enabled'], value)} />
        <CheckboxCard label="宫格分屏" hint="后续扩展" checked={config.canvas.grid_enabled} onChange={(value) => updateValue(['canvas', 'grid_enabled'], value)} />
      </div>
    </TogglePanel>
  )
}

/** 运动配置 */
function MotionSection({ config, updateValue, updateRange }: SectionProps) {
  return (
    <div className="space-y-4">
      <TogglePanel
        title="旋转与翻转"
        description="用于画面方向、镜像和轻微旋转。"
        enabled={config.transform.enabled}
        onToggle={(value) => updateValue(['transform', 'enabled'], value)}
      >
        <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
          <SelectField label="旋转" value={config.transform.rotate_mode} options={[['none', '不旋转'], ['left90', '左转 90 度'], ['right90', '右转 90 度']]} onChange={(value) => updateValue(['transform', 'rotate_mode'], value)} />
          <RangeField label="随机轻微旋转" value={config.transform.random_rotate} onChange={(updates) => updateRange(['transform', 'random_rotate'], updates)} />
        </div>
        <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
          <CheckboxCard label="水平翻转" checked={config.transform.flip_horizontal} onChange={(value) => updateValue(['transform', 'flip_horizontal'], value)} />
          <CheckboxCard label="垂直翻转" checked={config.transform.flip_vertical} onChange={(value) => updateValue(['transform', 'flip_vertical'], value)} />
          <CheckboxCard label="黑边去除" checked={config.transform.remove_black_bars} onChange={(value) => updateValue(['transform', 'remove_black_bars'], value)} />
          <CheckboxCard label="完整显示" checked={config.transform.show_full_frame} onChange={(value) => updateValue(['transform', 'show_full_frame'], value)} />
        </div>
      </TogglePanel>

      <TogglePanel
        title="帧率与动态变化"
        description="抽帧和动态缩放默认谨慎使用，避免明显影响观看体验。"
        enabled={config.timing.enabled}
        onToggle={(value) => updateValue(['timing', 'enabled'], value)}
      >
        <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
          <RangeField label="帧率" value={config.timing.fps} onChange={(updates) => updateRange(['timing', 'fps'], updates)} />
          <RangeField label="动态缩放" value={config.timing.dynamic_zoom} onChange={(updates) => updateRange(['timing', 'dynamic_zoom'], updates)} />
          <div className="rounded-md border border-border bg-background-elevated p-3">
            <CheckboxCard label="抽帧" checked={config.timing.drop_frame.enabled} onChange={(value) => updateValue(['timing', 'drop_frame', 'enabled'], value)} />
            <div className="mt-3">
              <RangeField label="每 N 帧抽一帧" value={config.timing.drop_frame.interval} onChange={(updates) => updateRange(['timing', 'drop_frame', 'interval'], updates)} />
            </div>
          </div>
        </div>
      </TogglePanel>
    </div>
  )
}

/** 输出配置 */
function OutputSection({ config, filterGraph, updateValue, updateRange }: SectionProps & { filterGraph: string }) {
  const acceleration = config.acceleration || { enabled: true, mode: 'auto', quality: 'size' }
  return (
    <div className="space-y-4">
      <TogglePanel
        title="CPU + GPU 混合加速"
        description="CPU 负责滤镜，GPU 负责编码；会实测 NVIDIA、Intel、AMD 编码器，优先使用真正可运行的 GPU。"
        enabled={acceleration.enabled}
        onToggle={(value) => updateValue(['acceleration', 'enabled'], value)}
      >
        <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
          <SelectField
            label="编码方式"
            value={acceleration.enabled ? acceleration.mode : 'cpu'}
            options={[
              ['auto', '自动选择 GPU'],
              ['cpu', 'CPU 稳定模式'],
              ['nvidia', 'NVIDIA NVENC'],
              ['intel', 'Intel QSV'],
              ['amd', 'AMD AMF'],
            ]}
            onChange={(value) => updateValue(['acceleration', 'mode'], value)}
          />
          <SelectField
            label="GPU 质量"
            value={acceleration.quality}
            options={[['size', '速度优先'], ['balanced', '均衡'], ['quality', '清晰优先']]}
            onChange={(value) => updateValue(['acceleration', 'quality'], value)}
          />
        </div>
      </TogglePanel>
      <TogglePanel
        title="码率与清晰度"
        description="固定码率更便于控制导出体积，倍率适合批量随机化。"
        enabled={config.bitrate.enabled}
        onToggle={(value) => updateValue(['bitrate', 'enabled'], value)}
      >
        <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
          <SelectField label="码率模式" value={config.bitrate.mode} options={[['fixed', '固定码率'], ['multiplier', '按倍率调整']]} onChange={(value) => updateValue(['bitrate', 'mode'], value)} />
          <SelectField label="快捷方案" value={config.bitrate.quality_mode} options={[['balanced', '均衡'], ['quality', '保持清晰优先'], ['size', '控制体积优先']]} onChange={(value) => updateValue(['bitrate', 'quality_mode'], value)} />
          <RangeField label="固定码率 kb/s" value={config.bitrate.fixed_kbps} onChange={(updates) => updateRange(['bitrate', 'fixed_kbps'], updates)} />
          <RangeField label="码率倍率" value={config.bitrate.multiplier} onChange={(updates) => updateRange(['bitrate', 'multiplier'], updates)} />
        </div>
      </TogglePanel>
      {filterGraph && <CodeBlock title="ffmpeg 滤镜参数" value={filterGraph} />}
    </div>
  )
}

/** 分组属性 */
interface SectionProps {
  config: ProcessingConfig
  updateValue: (path: string[], value: unknown) => void
  updateRange: (path: string[], updates: Partial<RandomRange>) => void
}

/** 右侧摘要 */
function SummaryPanel({ config, filterGraph }: { config: ProcessingConfig; filterGraph: string }) {
  return (
    <section className="sticky top-0 rounded-lg border border-border bg-background p-4">
      <SectionTitle title="当前方案" description="一键完成会使用这套参数。" />
      <div className="mt-4 grid grid-cols-2 gap-2">
        <SummaryMetric label="输出" value={resolutionLabel(config.canvas.resolution)} />
        <SummaryMetric label="画布" value={canvasModeLabel(config.canvas.mode)} />
        <SummaryMetric label="码率" value={config.bitrate.mode === 'fixed' ? `${rangeText(config.bitrate.fixed_kbps)} kb/s` : `${rangeText(config.bitrate.multiplier)}x`} />
        <SummaryMetric label="帧率" value={`${rangeText(config.timing.fps)} fps`} />
        <SummaryMetric label="编码" value={accelerationLabel(config.acceleration?.enabled === false ? 'cpu' : config.acceleration?.mode || 'auto')} />
      </div>
      <div className="mt-4 space-y-2 text-xs text-foreground-muted">
        <SummaryRow label="画面微调" value={config.adjustments.enabled ? '启用' : '关闭'} />
        <SummaryRow label="镜像" value={config.transform.flip_horizontal ? '水平翻转' : '不翻转'} />
        <SummaryRow label="旋转" value={config.transform.random_rotate.enabled ? rangeText(config.transform.random_rotate) : rotateLabel(config.transform.rotate_mode)} />
        <SummaryRow label="滤镜" value={filterGraph ? '已生成' : '未生成'} />
      </div>
    </section>
  )
}

/** 硬件加速标签 */
function accelerationLabel(value: 'auto' | 'cpu' | 'nvidia' | 'intel' | 'amd') {
  return {
    auto: '自动 GPU',
    cpu: 'CPU',
    nvidia: 'NVENC',
    intel: 'QSV',
    amd: 'AMF',
  }[String(value)] || '自动 GPU'
}

/** 面板容器 */
function TogglePanel({ title, description, enabled, onToggle, children }: { title: string; description: string; enabled: boolean; onToggle: (value: boolean) => void; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-background-elevated p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div>
          <h4 className="text-sm font-medium">{title}</h4>
          <p className="mt-1 text-xs text-foreground-muted">{description}</p>
        </div>
        <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-background px-3 text-xs text-foreground-muted">
          <input type="checkbox" checked={enabled} onChange={(event) => onToggle(event.target.checked)} className="accent-primary" />
          启用
        </label>
      </div>
      <div className={enabled ? '' : 'pointer-events-none opacity-45'}>{children}</div>
    </section>
  )
}

/** 随机范围输入组件 */
function RangeField({ label, value, onChange }: { label: string; value: RandomRange; onChange: (updates: Partial<RandomRange>) => void }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium" title={label}>{label}</span>
        <label className="flex items-center gap-1 text-[10px] text-foreground-muted">
          <input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ enabled: event.target.checked })} className="accent-primary" />
          启用
        </label>
      </div>
      <div className={value.enabled ? '' : 'pointer-events-none opacity-45'}>
        <div className="mb-2 grid grid-cols-2 gap-2">
          <NumberInput label="最小" value={value.min} onChange={(next) => onChange({ min: next })} />
          <NumberInput label="最大" value={value.max} onChange={(next) => onChange({ max: next })} />
        </div>
        <div className="grid grid-cols-[1fr_auto] items-end gap-2">
          <NumberInput label="固定值" value={value.value ?? value.min} onChange={(next) => onChange({ value: next, random: false })} />
          <label className="flex h-8 items-center gap-1 rounded-md border border-border px-2 text-[10px] text-foreground-muted">
            <input type="checkbox" checked={value.random} onChange={(event) => onChange({ random: event.target.checked })} className="accent-primary" />
            随机
          </label>
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

/** 文本输入 */
function TextField({ label, value, placeholder, className = '', onChange }: { label: string; value: string; placeholder?: string; className?: string; onChange: (value: string) => void }) {
  return (
    <label className={`block ${className}`}>
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

/** 数字输入组件 */
function NumberInput({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] text-foreground-muted">{label}</span>
      <input
        type="number"
        step="0.01"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-8 w-full rounded-md border border-border bg-background-elevated px-2 text-sm outline-none transition-colors focus:border-primary"
      />
    </label>
  )
}

/** 单个数字字段 */
function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      />
    </label>
  )
}

/** 下拉字段 */
function SelectField({ label, value, options, className = '', onChange }: { label: string; value: string; options: Array<[string, string]>; className?: string; onChange: (value: string) => void }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
  )
}

/** 复选卡片 */
function CheckboxCard({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`min-h-12 rounded-md border p-3 text-left transition-colors ${
        checked
          ? 'border-primary bg-primary/10 text-primary'
          : 'border-border bg-background text-foreground-muted hover:border-border-bright hover:text-foreground'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{label}</span>
        <span className={`h-2 w-2 rounded-full ${checked ? 'bg-primary' : 'bg-foreground-muted'}`} />
      </div>
      {hint && <div className="mt-1 text-[10px] opacity-80">{hint}</div>}
    </button>
  )
}

/** 参数代码块 */
function CodeBlock({ title, value }: { title: string; value: string }) {
  return (
    <section className="rounded-lg border border-border bg-background p-3">
      <h4 className="mb-2 text-sm font-medium">{title}</h4>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-background-elevated p-3 text-xs text-foreground-muted select-text">
        {value}
      </pre>
    </section>
  )
}

/** 摘要指标 */
function SummaryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background-elevated p-2">
      <div className="text-[10px] text-foreground-muted">{label}</div>
      <div className="truncate text-xs font-medium">{value}</div>
    </div>
  )
}

/** 摘要行 */
function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-background-elevated px-3 py-2">
      <span>{label}</span>
      <span className="min-w-0 truncate text-foreground">{value}</span>
    </div>
  )
}

/** 范围显示 */
function rangeText(value: RandomRange) {
  if (!value.enabled) return '关闭'
  if (value.random) return `${formatNumber(value.min)}-${formatNumber(value.max)}`
  return formatNumber(value.value ?? value.min)
}

/** 格式化数字 */
function formatNumber(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

/** 分辨率文案 */
function resolutionLabel(value: ProcessingConfig['canvas']['resolution']) {
  const labels = { '720p': '720p', '1080p': '1080p', original: '原分辨率', custom: '自定义' }
  return labels[value]
}

/** 画布模式文案 */
function canvasModeLabel(value: ProcessingConfig['canvas']['mode']) {
  const labels = { keep: '原比例', stretch: '拉伸', crop: '裁切', blur_background: '背景模糊' }
  return labels[value]
}

/** 旋转文案 */
function rotateLabel(value: ProcessingConfig['transform']['rotate_mode']) {
  const labels = { none: '不旋转', left90: '左转 90 度', right90: '右转 90 度' }
  return labels[value]
}
