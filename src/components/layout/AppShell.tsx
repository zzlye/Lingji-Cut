// src/components/layout/AppShell.tsx
// 主布局壳 - 顶部标题栏 + 左侧导航 + 主工作区 + 活动抽屉 + 设置浮层
import { useState } from 'react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { TitleBar } from './TitleBar'
import { NavRail } from './NavRail'
import { ActivityDrawer } from './ActivityDrawer'
import { StudioWorkspace } from '@/features/studio/StudioWorkspace'
import { QueueWorkspace } from '@/features/queue/QueueWorkspace'
import { LibraryPanel } from '@/features/library/LibraryPanel'
import { HistoryPanel } from '@/features/history/HistoryPanel'
import { SettingsCenter, type SettingsTab } from '@/features/settings/SettingsCenter'
import { useUiStore } from '@/stores/uiStore'
import { useAutomationStream } from '@/hooks/useAutomationStream'

/** 旧侧边栏项类型，保留以兼容尚未退役的 Sidebar 组件 */
export type SidebarItem = 'library' | 'tasks' | 'history'

export function AppShell() {
  // 全局唯一的自动化进度流（SSE）入口，取代 Header/TaskPanel 各自的监听
  useAutomationStream()
  const workspace = useUiStore((s) => s.workspace)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('effects')

  const openSettings = (tab: SettingsTab = 'effects') => {
    setSettingsTab(tab)
    setSettingsOpen(true)
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen flex-col overflow-hidden text-foreground">
        <TitleBar onOpenSettings={() => openSettings('effects')} />
        <div className="flex min-h-0 flex-1">
          <NavRail onOpenSettings={() => openSettings('effects')} />
          <main className="min-w-0 flex-1 overflow-auto">
            {workspace === 'queue' ? (
              <QueueWorkspace />
            ) : workspace === 'library' ? (
              <LibraryPanel />
            ) : workspace === 'history' ? (
              <HistoryPanel />
            ) : (
              <StudioWorkspace onOpenSettings={openSettings} />
            )}
          </main>
        </div>
        <ActivityDrawer />
        {settingsOpen && <SettingsOverlay tab={settingsTab} onClose={() => setSettingsOpen(false)} />}
      </div>
    </TooltipProvider>
  )
}

/** 过渡期：复用旧设置中心，以居中遮罩呈现（阶段四会改为独立设置工作区） */
function SettingsOverlay({ tab, onClose }: { tab: SettingsTab; onClose: () => void }) {
  return (
    <div
      className="no-drag fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={onClose}
    >
      <div
        className="glass-strong w-[min(1000px,100%)] overflow-hidden rounded-xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <SettingsCenter onClose={onClose} onDragStart={() => {}} initialTab={tab} />
      </div>
    </div>
  )
}
