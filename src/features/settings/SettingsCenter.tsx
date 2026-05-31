// src/features/settings/SettingsCenter.tsx
// 设置中心弹层 - 汇总 API、字幕、配音和文件位置配置

import { useEffect, useState } from 'react'
import { ApiConfigPanel } from './ApiConfigPanel'
import { SubtitleEditor } from '@/features/subtitle/SubtitleEditor'
import { VoiceConfigPanel } from '@/features/voice/VoiceConfigPanel'
import { settingsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'

/** 设置页签类型 */
type SettingsTab = 'api' | 'subtitle' | 'voice' | 'paths'

/** 设置中心属性 */
interface SettingsCenterProps {
  /** 关闭设置中心 */
  onClose: () => void
}

/**
 * 设置中心
 * 放在顶部齿轮弹层内，避免多个配置入口分散在侧边栏。
 */
export function SettingsCenter({ onClose }: SettingsCenterProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('api')

  return (
    <div className="max-h-[78vh] min-h-[560px] flex flex-col">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">设置</h3>
          <p className="text-xs text-foreground-muted">API、字幕、配音和项目文件夹</p>
        </div>
        <button
          onClick={onClose}
          className="h-9 w-9 border border-border rounded-md hover:bg-white/5"
          title="关闭设置"
          aria-label="关闭设置"
        >
          <svg className="w-4 h-4 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex flex-1 min-h-0">
        <nav className="w-40 border-r border-border p-3 space-y-1">
          <SettingsTabButton id="api" active={activeTab} onClick={setActiveTab} label="API 设置" />
          <SettingsTabButton id="subtitle" active={activeTab} onClick={setActiveTab} label="字幕设置" />
          <SettingsTabButton id="voice" active={activeTab} onClick={setActiveTab} label="配音配置" />
          <SettingsTabButton id="paths" active={activeTab} onClick={setActiveTab} label="文件位置" />
        </nav>

        <div className="flex-1 min-w-0 overflow-hidden">
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
      className={`w-full h-10 px-3 rounded-md text-left text-sm transition-colors ${
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
  const [paths, setPaths] = useState<Record<string, { path: string; exists: boolean }>>({})
  const { addLog } = useTaskStore()

  /** 加载项目文件夹位置 */
  const loadPaths = async () => {
    try {
      const data = await settingsApi.paths()
      setPaths(data)
    } catch (error) {
      addLog('error', `加载文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadPaths()
  }, [])

  const labels: Record<string, string> = {
    project_root: '项目目录',
    data_dir: '数据库目录',
    downloads_dir: '下载目录',
    output_dir: '输出目录',
    exports_dir: '导出目录',
    tools_dir: '工具目录',
    yt_dlp_path: 'yt-dlp',
    ffmpeg_path: 'ffmpeg',
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-medium">文件位置</h3>
        <button onClick={loadPaths} className="h-8 px-3 border border-border rounded-md text-xs hover:bg-white/5">
          刷新
        </button>
      </div>
      <div className="flex-1 overflow-auto p-4 space-y-2">
        {Object.entries(paths).map(([key, value]) => (
          <div key={key} className="p-3 bg-background rounded-lg border border-border">
            <div className="flex items-center justify-between gap-3 mb-1">
              <span className="text-sm font-medium">{labels[key] || key}</span>
              <span className={`text-xs ${value.exists ? 'text-success' : 'text-warning'}`}>
                {value.exists ? '存在' : '未找到'}
              </span>
            </div>
            <p className="text-xs text-foreground-muted break-all select-text">{value.path}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
