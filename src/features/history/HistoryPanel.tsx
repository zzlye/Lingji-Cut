// src/features/history/HistoryPanel.tsx
// 历史记录 - 展示所有已结束的一键流程（完成/失败/取消）
import { History as HistoryIcon, Trash2 } from 'lucide-react'
import { automationApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatClockTime } from '@/lib/format'
import { useAutomationStore } from '@/stores/automationStore'
import { useTaskStore } from '@/stores/taskStore'

/** 已结束状态 → 文案与色调 */
const FINISHED_META: Record<string, { label: string; variant: 'default' | 'destructive' | 'outline'; dot: string }> = {
  completed: { label: '已完成', variant: 'default', dot: 'bg-success' },
  failed: { label: '失败', variant: 'destructive', dot: 'bg-destructive' },
  cancelled: { label: '已取消', variant: 'outline', dot: 'bg-warning' },
}

export function HistoryPanel() {
  const jobs = useAutomationStore((s) => s.jobs)
  const removeJob = useAutomationStore((s) => s.removeJob)
  const { addLog } = useTaskStore()
  const history = jobs.filter((job) => job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled')

  const handleDelete = async (jobId: string, title: string) => {
    try {
      await automationApi.deleteJob(jobId)
      removeJob(jobId)
      addLog('info', `历史记录 "${title}" 已删除`)
    } catch (error) {
      addLog('error', `删除历史记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">历史记录</h2>
        <p className="text-sm text-muted-foreground">已结束的一键流程任务。</p>
      </div>

      {history.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
          <HistoryIcon className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">暂无历史记录</p>
          <p className="text-xs text-muted-foreground/70">完成或结束的任务会显示在这里</p>
        </div>
      ) : (
        <div className="space-y-2">
          {history.map((job) => {
            const meta = FINISHED_META[job.status] ?? FINISHED_META.completed
            const output = job.steps.find((step) => step.key === 'export')?.output_path
            return (
              <Card key={job.id}>
                <CardContent className="flex items-center gap-3 pt-6">
                  <span className={cn('size-2 shrink-0 rounded-full', meta.dot)} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{job.title}</p>
                    {output && <p className="truncate text-xs text-muted-foreground select-text">{output}</p>}
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatClockTime(job.completed_at || job.created_at)}</span>
                  <Badge variant={meta.variant} className="shrink-0">{meta.label}</Badge>
                  <Button size="sm" variant="outline" className="shrink-0 text-destructive" onClick={() => handleDelete(job.id, job.title)}>
                    <Trash2 className="mr-1.5 size-4" />
                    删除
                  </Button>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
