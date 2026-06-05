// src/components/layout/AppShell.tsx
// 主布局壳 - 顶部标题栏 + 左侧导航 + 主工作区 + 活动抽屉
import { TooltipProvider } from '@/components/ui/tooltip'
import { TitleBar } from './TitleBar'
import { NavRail } from './NavRail'
import { ActivityDrawer } from './ActivityDrawer'
import { StudioWorkspace } from '@/features/studio/StudioWorkspace'
import { QueueWorkspace } from '@/features/queue/QueueWorkspace'
import { LibraryPanel } from '@/features/library/LibraryPanel'
import { HistoryPanel } from '@/features/history/HistoryPanel'
import { SettingsWorkspace } from '@/features/settings/SettingsWorkspace'
import { SubtitleWorkbenchPage } from '@/features/subtitle/SubtitleWorkbenchPage'
import { useUiStore } from '@/stores/uiStore'
import { useAutomationStream } from '@/hooks/useAutomationStream'

export function AppShell() {
  // 全局唯一的自动化进度流（SSE）入口
  useAutomationStream()
  const workspace = useUiStore((s) => s.workspace)
  const openSettings = useUiStore((s) => s.openSettings)

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen flex-col overflow-hidden text-foreground">
        <TitleBar onOpenSettings={() => openSettings()} />
        <div className="flex min-h-0 flex-1">
          <NavRail onOpenSettings={() => openSettings()} />
          <main className="min-w-0 flex-1 overflow-auto">
            {workspace === 'queue' ? (
              <QueueWorkspace />
            ) : workspace === 'library' ? (
              <LibraryPanel />
            ) : workspace === 'subtitle' ? (
              <SubtitleWorkbenchPage />
            ) : workspace === 'history' ? (
              <HistoryPanel />
            ) : workspace === 'settings' ? (
              <SettingsWorkspace />
            ) : (
              <StudioWorkspace onOpenSettings={(section) => openSettings(section)} />
            )}
          </main>
        </div>
        <ActivityDrawer />
      </div>
    </TooltipProvider>
  )
}
