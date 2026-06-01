// src/features/settings/SettingsCenter.tsx
// 设置中心弹层 - 汇总画面处理、API、字幕、配音和文件位置配置

import { useEffect, useMemo, useState } from 'react'
import { ApiConfigPanel } from './ApiConfigPanel'
import { SubtitleEditor } from '@/features/subtitle/SubtitleEditor'
import { VoiceConfigPanel } from '@/features/voice/VoiceConfigPanel'
import { EffectsSettingsPanel } from '@/features/effects/EffectsPanel'
import { settingsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import type { ProjectPaths, ToolStatusMap } from '@/types'

/** 设置页签类型 */
type SettingsTab = 'effects' | 'api' | 'subtitle' | 'voice' | 'paths'

/** 设置中心属性 */
interface SettingsCenterProps {
  /** 关闭设置中心 */
  onClose: () => void
  /** 开始拖动设置窗口 */
  onDragStart: (event: React.MouseEvent<HTMLDivElement>) => void
}

/** 页签配置 */
const SETTINGS_TABS: Array<{ id: SettingsTab; label: string; description: string }> = [
  { id: 'effects', label: '画面处理', description: '差异化、画布和输出' },
  { id: 'api', label: 'API 设置', description: '文本模型渠道' },
  { id: 'subtitle', label: '字幕设置', description: '语言和字幕样式' },
  { id: 'voice', label: '配音配置', description: 'TTS 和声音' },
  { id: 'paths', label: '文件位置', description: '项目目录和子文件夹' },
]

/**
 * 设置中心
 * 放在顶部齿轮弹层内，避免多个配置入口分散在侧边栏。
 */
export function SettingsCenter({ onClose, onDragStart }: SettingsCenterProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('effects')
  const activeConfig = SETTINGS_TABS.find((tab) => tab.id === activeTab) || SETTINGS_TABS[0]

  return (
    <div className="flex h-[min(76vh,640px)] min-h-[480px] flex-col overflow-hidden">
      <div
        onMouseDown={onDragStart}
        className="flex cursor-move select-none items-center justify-between gap-3 border-b border-border px-4 py-3"
      >
        <div className="min-w-0">
          <h3 className="text-sm font-medium">设置</h3>
          <p className="truncate text-xs text-foreground-muted">{activeConfig.description}</p>
        </div>
        <button
          onClick={onClose}
          className="h-9 w-9 shrink-0 rounded-md border border-border hover:bg-white/5"
          title="关闭设置"
          aria-label="关闭设置"
        >
          <svg className="mx-auto h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex min-h-0 flex-1 max-md:flex-col">
        <nav className="w-44 shrink-0 border-r border-border p-2.5 max-md:w-full max-md:border-r-0 max-md:border-b">
          <div className="space-y-1 max-md:flex max-md:gap-1 max-md:space-y-0 max-md:overflow-x-auto">
            {SETTINGS_TABS.map((tab) => (
              <SettingsTabButton
                key={tab.id}
                id={tab.id}
                active={activeTab}
                onClick={setActiveTab}
                label={tab.label}
              />
            ))}
          </div>
        </nav>

        <div className="min-w-0 flex-1 overflow-hidden">
          {activeTab === 'effects' && <EffectsSettingsPanel variant="compact" />}
          {activeTab === 'api' && <ApiConfigPanel compact />}
          {activeTab === 'subtitle' && <SubtitleEditor compact />}
          {activeTab === 'voice' && <VoiceConfigPanel compact />}
          {activeTab === 'paths' && <FileLocationPanel />}
        </div>
      </div>
    </div>
  )
}

/** 设置页签按钮 */
function SettingsTabButton({ id, active, onClick, label }: { id: SettingsTab; active: SettingsTab; onClick: (id: SettingsTab) => void; label: string }) {
  return (
    <button
      onClick={() => onClick(id)}
      className={`min-h-10 w-full rounded-md px-3 py-2 text-left text-sm transition-colors max-md:w-auto max-md:shrink-0 max-md:whitespace-nowrap ${
        active === id
          ? 'bg-primary/20 text-primary'
          : 'text-foreground-muted hover:bg-white/5 hover:text-foreground'
      }`}
    >
      {label}
    </button>
  )
}

/** 文件位置面板 */
function FileLocationPanel() {
  const [paths, setPaths] = useState<ProjectPaths | null>(null)
  const [tools, setTools] = useState<ToolStatusMap | null>(null)
  const [projectRoot, setProjectRoot] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isLoadingTools, setIsLoadingTools] = useState(false)
  const { addLog } = useTaskStore()

  const subDirectories = useMemo(() => {
    if (!paths) return []
    return [
      ['下载目录', paths.downloads_dir],
      ['处理中间文件', paths.output_dir],
      ['导出成品', paths.exports_dir],
      ['数据目录', paths.data_dir],
    ] as const
  }, [paths])

  /** 加载项目文件夹位置 */
  const loadPaths = async () => {
    try {
      const data = await settingsApi.paths()
      setPaths(data)
      setProjectRoot(data.project_root?.path || '')
    } catch (error) {
      addLog('error', `加载文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 加载自动化依赖工具状态 */
  const loadTools = async () => {
    setIsLoadingTools(true)
    try {
      setTools(await settingsApi.tools())
    } catch (error) {
      addLog('error', `加载工具状态失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsLoadingTools(false)
    }
  }

  useEffect(() => {
    loadPaths()
    loadTools()
  }, [])

  /** 使用 Electron 原生目录选择器 */
  const handleSelectDirectory = async () => {
    const picker = window.electron?.dialog?.selectDirectory
    if (!picker) {
      addLog('warn', '当前浏览器预览环境不支持系统目录选择，请直接输入项目目录路径')
      return
    }

    try {
      const selected = await picker(projectRoot)
      if (selected) setProjectRoot(selected)
    } catch (error) {
      addLog('error', `选择目录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 保存项目目录并创建子目录 */
  const handleSave = async () => {
    if (!projectRoot.trim()) {
      addLog('warn', '请输入项目目录')
      return
    }

    setIsSaving(true)
    try {
      const data = await settingsApi.updatePaths(projectRoot)
      setPaths(data)
      setProjectRoot(data.project_root.path)
      addLog('info', `项目目录已保存: ${data.project_root.path}`)
    } catch (error) {
      addLog('error', `保存文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  /** 恢复默认目录 */
  const handleReset = async () => {
    setIsSaving(true)
    try {
      const data = await settingsApi.resetPaths()
      setPaths(data)
      setProjectRoot(data.project_root.path)
      addLog('info', '项目目录已恢复默认')
    } catch (error) {
      addLog('error', `恢复默认目录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-medium">文件位置</h3>
        <p className="mt-1 text-xs text-foreground-muted">设置一键流程使用的项目目录，保存后会自动创建业务子文件夹。</p>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(360px,1fr)_minmax(240px,320px)] max-lg:grid-cols-1 gap-4">
          <section className="rounded-lg border border-border bg-background p-4">
            <div className="mb-4">
              <h4 className="text-sm font-medium">项目目录</h4>
              <p className="mt-1 text-xs text-foreground-muted">下载、字幕、配音、中间文件和导出文件会按此目录归档。</p>
            </div>

            <label className="block">
              <span className="mb-1 block text-xs text-foreground-muted">文件夹路径</span>
              <div className="flex flex-wrap gap-2">
                <input
                  value={projectRoot}
                  onChange={(event) => setProjectRoot(event.target.value)}
                  placeholder="例如 D:\视频项目\YouTube"
                  className="h-10 min-w-64 flex-1 rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
                />
                <button
                  onClick={handleSelectDirectory}
                  className="h-10 shrink-0 whitespace-nowrap rounded-md border border-border px-4 text-sm hover:bg-white/5"
                >
                  选择文件夹
                </button>
              </div>
            </label>

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="h-10 min-w-36 whitespace-nowrap rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSaving ? '保存中...' : '保存并创建目录'}
              </button>
              <button
                onClick={handleReset}
                disabled={isSaving}
                className="h-10 min-w-24 whitespace-nowrap rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                恢复默认
              </button>
              <button
                onClick={loadPaths}
                className="h-10 min-w-24 whitespace-nowrap rounded-md border border-border px-4 text-sm text-foreground-muted hover:bg-white/5 hover:text-foreground"
              >
                重新读取
              </button>
            </div>
          </section>

          <div className="space-y-4">
            <section className="rounded-lg border border-border bg-background p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h4 className="text-sm font-medium">自动化工具</h4>
                  <p className="mt-1 text-xs text-foreground-muted">优先读取 D:\tools，找不到时回退 PATH。</p>
                </div>
                <button
                  onClick={loadTools}
                  disabled={isLoadingTools}
                  className="h-8 shrink-0 rounded-md border border-border px-3 text-xs hover:bg-white/5 disabled:opacity-50"
                >
                  {isLoadingTools ? '检测中...' : '检测'}
                </button>
              </div>
              <div className="mt-4 space-y-2">
                {tools ? (
                  Object.entries(tools).map(([key, info]) => (
                    <ToolStatusCard key={key} label={key === 'yt_dlp' ? 'yt-dlp' : 'ffmpeg'} info={info} />
                  ))
                ) : (
                  <div className="rounded-md border border-dashed border-border p-3 text-xs text-foreground-muted">
                    正在检测 yt-dlp 和 ffmpeg...
                  </div>
                )}
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <h4 className="text-sm font-medium">自动子目录</h4>
              <p className="mt-1 text-xs text-foreground-muted">保存时创建，后续任务会直接使用。</p>
              <div className="mt-4 space-y-2">
                {subDirectories.map(([label, info]) => (
                  <div key={label} className="rounded-md border border-border bg-background-elevated p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium">{label}</span>
                      <span className={`text-[10px] ${info.exists ? 'text-success' : 'text-warning'}`}>
                        {info.exists ? '已创建' : '保存后创建'}
                      </span>
                    </div>
                    <p className="mt-1 break-all text-xs text-foreground-muted select-text">{info.path}</p>
                  </div>
                ))}
                {!paths && (
                  <div className="rounded-md border border-dashed border-border p-3 text-xs text-foreground-muted">
                    正在读取项目目录...
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 工具状态卡片 */
function ToolStatusCard({ label, info }: { label: string; info: ToolStatusMap[keyof ToolStatusMap] }) {
  return (
    <div className="rounded-md border border-border bg-background-elevated p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{label}</span>
        <span className={`text-[10px] ${info.available ? 'text-success' : 'text-destructive'}`}>
          {info.available ? `可用 · ${info.source}` : '不可用'}
        </span>
      </div>
      <p className="mt-1 break-all text-xs text-foreground-muted select-text">{info.command}</p>
      {info.version && <p className="mt-1 line-clamp-2 text-[10px] text-foreground-muted">{info.version}</p>}
      {info.error_message && <p className="mt-1 text-[10px] text-destructive">{info.error_message}</p>}
    </div>
  )
}
