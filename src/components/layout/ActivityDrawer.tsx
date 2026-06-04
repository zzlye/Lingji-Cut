// src/components/layout/ActivityDrawer.tsx
// 活动日志抽屉 - 从右侧滑出，集中查看历史日志（瞬时通知由 Toast 负责）
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatClockTime } from '@/lib/format'
import { useUiStore } from '@/stores/uiStore'
import { useLogStore } from '@/stores/logStore'

export function ActivityDrawer() {
  const isOpen = useUiStore((s) => s.isActivityOpen)
  const setActivityOpen = useUiStore((s) => s.setActivityOpen)
  const logs = useLogStore((s) => s.logs)
  const clearLogs = useLogStore((s) => s.clearLogs)

  return (
    <Sheet open={isOpen} onOpenChange={setActivityOpen}>
      <SheetContent className="glass-strong flex w-[420px] flex-col gap-0 p-0 sm:max-w-[420px]">
        <SheetHeader className="border-b px-4 py-3">
          <SheetTitle className="text-sm">活动日志</SheetTitle>
        </SheetHeader>

        <ScrollArea className="min-h-0 flex-1 px-4 py-3">
          {logs.length === 0 ? (
            <p className="text-xs text-muted-foreground">暂无日志记录</p>
          ) : (
            <div className="space-y-1.5">
              {[...logs].reverse().map((log, index) => (
                <div key={index} className="flex gap-2 text-xs leading-relaxed">
                  <span className="shrink-0 tabular-nums text-muted-foreground">{formatClockTime(log.timestamp)}</span>
                  <span
                    className={cn(
                      log.level === 'error'
                        ? 'text-destructive'
                        : log.level === 'warn'
                          ? 'text-warning'
                          : 'text-foreground/80',
                    )}
                  >
                    {log.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>

        <div className="border-t p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={clearLogs}>
            清空日志
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  )
}
