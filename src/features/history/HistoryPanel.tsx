// src/features/history/HistoryPanel.tsx
// 历史记录 - 展示所有已结束的一键流程（完成/失败/取消）
import { useEffect, useState } from 'react'
import { History as HistoryIcon, Trash2 } from 'lucide-react'
import { automationApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatClockTime } from '@/lib/format'
import { useAutomationStore } from '@/stores/automationStore'
import { useTaskStore } from '@/stores/taskStore'
import { useUiStore } from '@/stores/uiStore'

/** 已结束状态 → 文案与色调 */
const FINISHED_META: Record<string, { label: string; variant: 'default' | 'destructive' | 'outline'; dot: string }> = {
  completed: { label: '已完成', variant: 'default', dot: 'bg-success' },
  failed: { label: '失败', variant: 'destructive', dot: 'bg-destructive' },
  cancelled: { label: '已取消', variant: 'outline', dot: 'bg-warning' },
}

export function HistoryPanel() {
  const [isClearing, setIsClearing] = useState(false)
  const jobs = useAutomationStore((s) => s.jobs)
  const removeJob = useAutomationStore((s) => s.removeJob)
  const syncBackendJobs = useAutomationStore((s) => s.syncBackendJobs)
  const { addLog } = useTaskStore()
  const openSubtitleWorkbench = useUiStore((s) => s.openSubtitleWorkbench)
  const history = jobs.filter((job) => job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled')

  useEffect(() => {
    automationApi.listJobs()
      .then(syncBackendJobs)
      .catch((error) => addLog('warn', `刷新历史记录失败: ${error instanceof Error ? error.message : '未知错误'}`))
  }, [addLog, syncBackendJobs])

  const handleDelete = async (jobId: string, title: string) => {
    try {
      await automationApi.deleteJob(jobId)
      removeJob(jobId)
      addLog('info', `历史记录 "${title}" 已删除`)
    } catch (error) {
      addLog('error', `删除历史记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const handleClearAll = async () => {
    if (history.length === 0 || isClearing) return
    setIsClearing(true)
    try {
      const results = await Promise.allSettled(
        history.map(async (job) => {
          await automationApi.deleteJob(job.id)
          return job
        }),
      )
      const succeeded = results.filter((result) => result.status === 'fulfilled')
      const failed = results.length - succeeded.length

      succeeded.forEach((result) => {
        if (result.status === 'fulfilled') {
          removeJob(result.value.id)
        }
      })

      if (succeeded.length > 0) {
        addLog('info', `已清空 ${succeeded.length} 条历史记录`)
      }
      if (failed > 0) {
        addLog('warn', `有 ${failed} 条历史记录清空失败，请稍后重试`)
      }
    } catch (error) {
      addLog('error', `清空历史记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsClearing(false)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">历史记录</h2>
          <p className="text-sm text-muted-foreground">已结束的一键流程任务。</p>
        </div>
        <Button variant="outline" className="text-destructive" disabled={history.length === 0 || isClearing} onClick={handleClearAll}>
          <Trash2 className="mr-1.5 size-4" />
          {isClearing ? '清空中…' : '一键清空'}
        </Button>
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
                  <Button size="sm" variant="outline" className="shrink-0" onClick={() => openSubtitleWorkbench(job.id)}>
                    字幕调整
                  </Button>
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
