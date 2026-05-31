// src/features/effects/EffectsPanel.tsx
// 画面处理面板 - 配置视频增强/差异化处理参数

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

/** 默认画面处理配置 */
export const createDefaultProcessingConfig = (): ProcessingConfig => ({
  adjustments: {
    enabled: true,
    brightness: range(0, 0.1),
    contrast: range(1, 1.2),
    saturation: range(1, 1.1),
    sharpness: range(0.9, 1.4),
    denoise: range(1, 2),
  },
  canvas: {
    enabled: true,
    resolution: '720p',
    mode: 'keep',
    width: 1280,
    height: 720,
    background_enabled: false,
    reflection_enabled: false,
    grid_enabled: false,
  },
  transform: {
    enabled: true,
    rotate_mode: 'none',
    flip_horizontal: true,
    flip_vertical: false,
    random_rotate: range(-1, 1),
    remove_black_bars: false,
    show_full_frame: true,
  },
  timing: {
    enabled: true,
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
    fixed_kbps: range(2000, 2000, 2000, false),
    multiplier: range(1.05, 1.95),
    quality_mode: 'balanced',
  },
})

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
    return saved ? JSON.parse(saved) : createDefaultProcessingConfig()
  } catch {
    return createDefaultProcessingConfig()
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
 * 提供类似传统视频处理工具的密集参数配置。
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
      for (const key of path) {
        target = target[key]
      }
      Object.assign(target, updates)
      return next
    })
  }

  /** 更新普通配置字段 */
  const updateValue = (path: string[], value: unknown) => {
    setConfig((current) => {
      const next = structuredClone(current)
      let target: any = next
      for (let i = 0; i < path.length - 1; i++) {
        target = target[path[i]]
      }
      target[path[path.length - 1]] = value
      return next
    })
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

  const isCompact = variant === 'compact'

  return (
    <div className={isCompact ? 'max-h-[78vh] flex flex-col' : 'h-full flex flex-col'}>
      <div className={`${isCompact ? 'px-4 py-3' : 'px-4 py-3 border-b border-border'} flex items-center justify-between gap-3`}>
        <div>
          <h3 className="text-sm font-medium">{isCompact ? '自动化参数设置' : '画面处理'}</h3>
          <p className="text-xs text-foreground-muted">设置参数、查看预览，然后由一键完成流程自动执行</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleBuildFilter} className="h-9 px-3 border border-border rounded-md text-sm hover:bg-white/5">
            生成参数
          </button>
          <button onClick={handlePreview} disabled={isBusy} className="h-9 px-3 border border-border rounded-md text-sm hover:bg-white/5 disabled:opacity-50">
            预览
          </button>
          <button onClick={handleApply} disabled={isBusy} className="h-9 px-4 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50">
            {isBusy ? '处理中...' : '开始处理'}
          </button>
          {onClose && (
            <button onClick={onClose} className="h-9 w-9 border border-border rounded-md text-sm hover:bg-white/5" title="关闭设置" aria-label="关闭设置">
              <svg className="w-4 h-4 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4">
        <section className="grid grid-cols-[1fr_220px_120px] gap-3">
          <label className="block">
            <span className="text-xs text-foreground-muted mb-1 block">视频路径</span>
            <input
              value={videoPath}
              onChange={(event) => setVideoPath(event.target.value)}
              placeholder="下载完成后会自动填入，也可以手动粘贴本地视频路径"
              className="w-full h-10 px-3 bg-background border border-border rounded-md text-sm"
            />
          </label>
          <label className="block">
            <span className="text-xs text-foreground-muted mb-1 block">已有预设</span>
            <select onChange={(event) => handleSelectPreset(event.target.value)} className="w-full h-10 px-3 bg-background border border-border rounded-md text-sm">
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>{preset.name}</option>
              ))}
            </select>
          </label>
          <button onClick={() => setConfig(createDefaultProcessingConfig())} className="self-end h-10 border border-border rounded-md text-sm hover:bg-white/5">
            重置
          </button>
        </section>

        <section className="grid grid-cols-[1fr_160px] gap-3">
          <input
            value={presetName}
            onChange={(event) => setPresetName(event.target.value)}
            placeholder="预设名称"
            className="h-10 px-3 bg-background border border-border rounded-md text-sm"
          />
          <button onClick={handleSavePreset} className="h-10 bg-accent text-accent-foreground rounded-md text-sm font-medium hover:bg-accent/90">
            保存模板
          </button>
        </section>

        <Panel title="画面调整（不同视频画面随机微调）" enabled={config.adjustments.enabled} onToggle={(value) => updateValue(['adjustments', 'enabled'], value)}>
          <div className={`grid ${isCompact ? 'grid-cols-1' : 'grid-cols-2'} gap-3`}>
            <RangeField label="亮度" value={config.adjustments.brightness} onChange={(updates) => updateRange(['adjustments', 'brightness'], updates)} />
            <RangeField label="对比度" value={config.adjustments.contrast} onChange={(updates) => updateRange(['adjustments', 'contrast'], updates)} />
            <RangeField label="饱和度" value={config.adjustments.saturation} onChange={(updates) => updateRange(['adjustments', 'saturation'], updates)} />
            <RangeField label="锐化" value={config.adjustments.sharpness} onChange={(updates) => updateRange(['adjustments', 'sharpness'], updates)} />
            <RangeField label="降噪" value={config.adjustments.denoise} onChange={(updates) => updateRange(['adjustments', 'denoise'], updates)} />
          </div>
        </Panel>

        <Panel title="分辨率与画布" enabled={config.canvas.enabled} onToggle={(value) => updateValue(['canvas', 'enabled'], value)}>
          <div className={`grid ${isCompact ? 'grid-cols-2' : 'grid-cols-4'} gap-3`}>
            <SelectField label="分辨率" value={config.canvas.resolution} options={[['720p', '720p'], ['1080p', '1080p'], ['original', '原分辨率'], ['custom', '自定义']]} onChange={(value) => updateValue(['canvas', 'resolution'], value)} />
            <SelectField label="模式" value={config.canvas.mode} options={[['keep', '原比例'], ['stretch', '拉伸'], ['crop', '裁切'], ['blur_background', '背景模糊']]} onChange={(value) => updateValue(['canvas', 'mode'], value)} />
            <NumberField label="宽度" value={config.canvas.width} onChange={(value) => updateValue(['canvas', 'width'], value)} />
            <NumberField label="高度" value={config.canvas.height} onChange={(value) => updateValue(['canvas', 'height'], value)} />
          </div>
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <Checkbox label="背景" checked={config.canvas.background_enabled} onChange={(value) => updateValue(['canvas', 'background_enabled'], value)} />
            <Checkbox label="倒影" checked={config.canvas.reflection_enabled} onChange={(value) => updateValue(['canvas', 'reflection_enabled'], value)} />
            <Checkbox label="宫格分屏（后续扩展）" checked={config.canvas.grid_enabled} onChange={(value) => updateValue(['canvas', 'grid_enabled'], value)} />
          </div>
        </Panel>

        <Panel title="旋转与翻转" enabled={config.transform.enabled} onToggle={(value) => updateValue(['transform', 'enabled'], value)}>
          <div className={`grid ${isCompact ? 'grid-cols-1' : 'grid-cols-2'} gap-3`}>
            <SelectField label="旋转" value={config.transform.rotate_mode} options={[['none', '不旋转'], ['left90', '左转90度'], ['right90', '右转90度']]} onChange={(value) => updateValue(['transform', 'rotate_mode'], value)} />
            <RangeField label="随机轻微旋转" value={config.transform.random_rotate} onChange={(updates) => updateRange(['transform', 'random_rotate'], updates)} />
          </div>
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <Checkbox label="水平翻转" checked={config.transform.flip_horizontal} onChange={(value) => updateValue(['transform', 'flip_horizontal'], value)} />
            <Checkbox label="垂直翻转" checked={config.transform.flip_vertical} onChange={(value) => updateValue(['transform', 'flip_vertical'], value)} />
            <Checkbox label="黑边去除" checked={config.transform.remove_black_bars} onChange={(value) => updateValue(['transform', 'remove_black_bars'], value)} />
            <Checkbox label="完全显示" checked={config.transform.show_full_frame} onChange={(value) => updateValue(['transform', 'show_full_frame'], value)} />
          </div>
        </Panel>

        <Panel title="帧率与时长变化" enabled={config.timing.enabled} onToggle={(value) => updateValue(['timing', 'enabled'], value)}>
          <div className={`grid ${isCompact ? 'grid-cols-1' : 'grid-cols-2'} gap-3`}>
            <RangeField label="帧率" value={config.timing.fps} onChange={(updates) => updateRange(['timing', 'fps'], updates)} />
            <RangeField label="动态缩放" value={config.timing.dynamic_zoom} onChange={(updates) => updateRange(['timing', 'dynamic_zoom'], updates)} />
          </div>
          <div className="mt-3 p-3 bg-background rounded-md border border-border">
            <Checkbox label="抽帧" checked={config.timing.drop_frame.enabled} onChange={(value) => updateValue(['timing', 'drop_frame', 'enabled'], value)} />
            <div className="mt-3">
              <RangeField label="每 N 帧抽一帧" value={config.timing.drop_frame.interval} onChange={(updates) => updateRange(['timing', 'drop_frame', 'interval'], updates)} />
            </div>
          </div>
        </Panel>

        <Panel title="码率与清晰度" enabled={config.bitrate.enabled} onToggle={(value) => updateValue(['bitrate', 'enabled'], value)}>
          <div className={`grid ${isCompact ? 'grid-cols-1' : 'grid-cols-3'} gap-3`}>
            <SelectField label="模式" value={config.bitrate.mode} options={[['fixed', '定值'], ['multiplier', '倍率']]} onChange={(value) => updateValue(['bitrate', 'mode'], value)} />
            <SelectField label="方案" value={config.bitrate.quality_mode} options={[['balanced', '均衡'], ['quality', '保持清晰优先'], ['size', '控制体积优先']]} onChange={(value) => updateValue(['bitrate', 'quality_mode'], value)} />
            <RangeField label="固定码率 kb/s" value={config.bitrate.fixed_kbps} onChange={(updates) => updateRange(['bitrate', 'fixed_kbps'], updates)} />
          </div>
          <div className="mt-3">
            <RangeField label="码率倍率" value={config.bitrate.multiplier} onChange={(updates) => updateRange(['bitrate', 'multiplier'], updates)} />
          </div>
        </Panel>

        {filterGraph && (
          <section className="bg-background-elevated border border-border rounded-lg p-4">
            <h4 className="text-sm font-medium mb-2">ffmpeg 滤镜参数</h4>
            <pre className="text-xs text-foreground-muted whitespace-pre-wrap break-all bg-background rounded-md border border-border p-3 select-text">
              {filterGraph}
            </pre>
          </section>
        )}
      </div>
    </div>
  )
}

/** 面板容器 */
function Panel({ title, enabled, onToggle, children }: { title: string; enabled: boolean; onToggle: (value: boolean) => void; children: React.ReactNode }) {
  return (
    <section className="bg-background-elevated border border-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={enabled} onChange={(event) => onToggle(event.target.checked)} />
          {title}
        </label>
      </div>
      <div className={enabled ? '' : 'opacity-50 pointer-events-none'}>{children}</div>
    </section>
  )
}

/** 随机范围输入组件 */
function RangeField({ label, value, onChange }: { label: string; value: RandomRange; onChange: (updates: Partial<RandomRange>) => void }) {
  return (
    <div className="grid grid-cols-[88px_1fr_1fr_72px] items-end gap-2">
      <label className="text-xs text-foreground-muted pb-2">{label}</label>
      <NumberInput label="最小" value={value.min} onChange={(next) => onChange({ min: next })} />
      <NumberInput label="最大" value={value.max} onChange={(next) => onChange({ max: next })} />
      <label className="flex items-center gap-1 text-xs text-foreground-muted pb-2">
        <input type="checkbox" checked={value.random} onChange={(event) => onChange({ random: event.target.checked })} />
        随机
      </label>
    </div>
  )
}

/** 数字输入组件 */
function NumberInput({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-[10px] text-foreground-muted block mb-1">{label}</span>
      <input
        type="number"
        step="0.01"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full h-9 px-2 bg-background border border-border rounded-md text-sm"
      />
    </label>
  )
}

/** 单个数字字段 */
function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-foreground-muted mb-1 block">{label}</span>
      <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm" />
    </label>
  )
}

/** 下拉字段 */
function SelectField({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string]>; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-foreground-muted mb-1 block">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm">
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
  )
}

/** 复选框字段 */
function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="inline-flex items-center gap-2 text-foreground-muted">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  )
}
