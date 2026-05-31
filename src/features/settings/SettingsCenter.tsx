// src/features/settings/SettingsCenter.tsx
// 设置中心弹层 - 汇总 API、字幕、配音和文件位置配置

import { useEffect, useState } from 'react'
import { ApiConfigPanel } from './ApiConfigPanel'
import { SubtitleEditor } from '@/features/subtitle/SubtitleEditor'
import { VoiceConfigPanel } from '@/features/voice/VoiceConfigPanel'
import { EffectsSettingsPanel } from '@/features/effects/EffectsPanel'
import { settingsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'

/** 设置页签类型 */
type SettingsTab = 'effects' | 'api' | 'subtitle' | 'voice' | 'paths'

/** 设置中心属性 */
interface SettingsCenterProps {
  /** 关闭设置中心 */
  onClose: () => void
  /** 开始拖动设置窗口 */
  onDragStart: (event: React.MouseEvent<HTMLDivElement>) => void
}

/**
 * 设置中心
 * 放在顶部齿轮弹层内，避免多个配置入口分散在侧边栏。
 */
export function SettingsCenter({ onClose, onDragStart }: SettingsCenterProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('effects')

  return (
    <div className="h-[600px] max-h-[78vh] flex flex-col">
      <div
        onMouseDown={onDragStart}
        className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 cursor-move select-none"
      >
        <div>
          <h3 className="text-sm font-medium">设置</h3>
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
        <nav className="w-40 border-r border-border p-2.5 space-y-1">
          <SettingsTabButton id="effects" active={activeTab} onClick={setActiveTab} label="画面处理" />
          <SettingsTabButton id="api" active={activeTab} onClick={setActiveTab} label="API 设置" />
          <SettingsTabButton id="subtitle" active={activeTab} onClick={setActiveTab} label="字幕设置" />
          <SettingsTabButton id="voice" active={activeTab} onClick={setActiveTab} label="配音配置" />
          <SettingsTabButton id="paths" active={activeTab} onClick={setActiveTab} label="文件位置" />
        </nav>

        <div className="flex-1 min-w-0 overflow-hidden">
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
      className={`w-full min-h-10 px-3 py-2 rounded-md text-left text-sm transition-colors ${
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
  const [projectRoot, setProjectRoot] = useState('')
  const { addLog } = useTaskStore()

  /** 加载项目文件夹位置 */
  const loadPaths = async () => {
    try {
      const data = await settingsApi.paths()
      setProjectRoot(data.project_root?.path || '')
    } catch (error) {
      addLog('error', `加载文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadPaths()
  }, [])

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-medium">文件位置</h3>
        <button onClick={loadPaths} className="h-8 px-3 border border-border rounded-md text-xs hover:bg-white/5">
          刷新
        </button>
      </div>
      <div className="flex-1 overflow-auto p-4">
        <div className="p-3 bg-background rounded-lg border border-border">
          <div className="mb-1 text-sm font-medium">项目目录</div>
          <p className="text-xs text-foreground-muted break-all select-text">
            {projectRoot || '正在读取项目目录...'}
          </p>
          <p className="mt-2 text-xs text-foreground-muted">
            下载、输出、导出和数据库子文件夹会自动创建。
          </p>
        </div>
      </div>
    </div>
  )
}
