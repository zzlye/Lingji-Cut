// src/components/layout/TitleBar.tsx
// 顶部标题栏 - 应用名、全局运行状态、活动日志与设置入口、窗口控制（无边框窗口可拖拽）
import { Clapperboard, ScrollText, Settings, Minus, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useAutomationStore } from '@/stores/automationStore'
import { useUiStore } from '@/stores/uiStore'

export function TitleBar({ onOpenSettings }: { onOpenSettings: () => void }) {
  const runningCount = useAutomationStore((s) => s.jobs.filter((job) => job.status === 'running').length)
  const toggleActivity = useUiStore((s) => s.toggleActivity)

  return (
    <header className="titlebar-drag glass relative z-20 flex h-11 items-center gap-3 border-b px-3">
      {/* 应用标识 */}
      <div className="no-drag flex items-center gap-2">
        <span className="grid size-6 place-items-center rounded-md bg-primary/20 text-primary">
          <Clapperboard className="size-4" />
        </span>
        <span className="text-sm font-semibold">灵剪工坊</span>
      </div>

      <div className="flex-1" />

      {/* 操作区 */}
      <div className="no-drag flex items-center gap-1.5">
        {runningCount > 0 && (
          <Badge variant="secondary" className="gap-1.5">
            <span className="size-1.5 animate-pulse rounded-full bg-info" />
            {runningCount} 进行中
          </Badge>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" onClick={toggleActivity} aria-label="活动日志">
              <ScrollText />
            </Button>
          </TooltipTrigger>
          <TooltipContent>活动日志</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" onClick={onOpenSettings} aria-label="设置">
              <Settings />
            </Button>
          </TooltipTrigger>
          <TooltipContent>设置</TooltipContent>
        </Tooltip>

        <Separator orientation="vertical" className="mx-1 h-5" />

        {/* 窗口控制 */}
        <Button variant="ghost" size="icon-sm" onClick={() => window.electron?.window.minimize()} aria-label="最小化">
          <Minus />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => window.electron?.window.maximize()} aria-label="最大化">
          <Square className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="hover:bg-destructive hover:text-white"
          onClick={() => window.electron?.window.close()}
          aria-label="关闭"
        >
          <X />
        </Button>
      </div>
    </header>
  )
}
