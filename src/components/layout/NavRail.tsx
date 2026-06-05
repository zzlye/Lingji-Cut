// src/components/layout/NavRail.tsx
// 左侧导航栏 - 在工作区间切换，毛玻璃质感
import { LayoutDashboard, ListChecks, FolderOpen, Captions, History, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useUiStore, type Workspace } from '@/stores/uiStore'

/** 主导航项 */
const NAV_ITEMS: Array<{ key: Workspace; label: string; icon: typeof LayoutDashboard }> = [
  { key: 'studio', label: '工作台', icon: LayoutDashboard },
  { key: 'queue', label: '任务队列', icon: ListChecks },
  { key: 'library', label: '素材库', icon: FolderOpen },
  { key: 'subtitle', label: '字幕调整', icon: Captions },
  { key: 'history', label: '历史记录', icon: History },
]

export function NavRail({ onOpenSettings }: { onOpenSettings: () => void }) {
  const workspace = useUiStore((s) => s.workspace)
  const setWorkspace = useUiStore((s) => s.setWorkspace)

  return (
    <nav className="glass z-10 flex w-56 shrink-0 flex-col gap-1 border-r p-3">
      {NAV_ITEMS.map((item) => {
        const active = workspace === item.key
        const Icon = item.icon
        return (
          <button
            key={item.key}
            onClick={() => setWorkspace(item.key)}
            className={cn(
              'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
              active
                ? 'bg-primary/15 font-medium text-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            <Icon className="size-5" />
            {item.label}
          </button>
        )
      })}

      <div className="flex-1" />

      <button
        onClick={onOpenSettings}
        className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <Settings className="size-5" />
        设置
      </button>
    </nav>
  )
}
