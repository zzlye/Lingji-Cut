// src/features/queue/QueueWorkspace.tsx
// 任务队列工作区 - 只展示一键流程任务，进度由全局 useAutomationStream(SSE) 维护
import { useEffect, useState } from 'react'
import {
  Play, Pause, X, RotateCcw, SkipForward, RefreshCw, Inbox,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import { automationApi } from '@/lib/api'
import { useAutomationStore } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import { useUiStore } from '@/stores/uiStore'
import type { AutomationJob } from '@/types'

/** 状态 → 文案 + 徽标样式 */
const STATUS_META: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; tone: string }> = {
  pending: { label: '等待', variant: 'outline', tone: 'text-muted-foreground' },
  downloading: { label: '下载中', variant: 'secondary', tone: 'text-info' },
  processing: { label: '处理中', variant: 'secondary', tone: 'text-info' },
  running: { label: '运行中', variant: 'secondary', tone: 'text-info' },
  paused: { label: '已暂停', variant: 'outline', tone: 'text-warning' },
  cancelled: { label: '已取消', variant: 'outline', tone: 'text-muted-foreground' },
  completed: { label: '已完成', variant: 'default', tone: 'text-success' },
  failed: { label: '失败', variant: 'destructive', tone: 'text-destructive' },
  skipped: { label: '已跳过', variant: 'outline', tone: 'text-muted-foreground' },
}

/** 待确认操作 */
type PendingConfirm = { title: string; description: string; action: () => void }

export function QueueWorkspace() {
  const jobs = useAutomationStore((s) => s.jobs)
  const syncBackendJobs = useAutomationStore((s) => s.syncBackendJobs)
  const addLog = useLogStore((s) => s.addLog)
  const openSubtitleWorkbench = useUiStore((s) => s.openSubtitleWorkbench)

  const [busyId, setBusyId] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null)

  /** 刷新自动流程任务（兜底，常态由全局 SSE 推送） */
  const refreshJobs = async () => {
    try {
      syncBackendJobs(await automationApi.listJobs())
    } catch {
      // 后端短暂不可用时静默，下一轮再试
    }
  }

  useEffect(() => {
    refreshJobs()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 有活跃任务时做兜底轮询；常态进度由全局 SSE 推送。
  useEffect(() => {
    const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'pending')
    if (!hasActiveJobs) return
    const timer = window.setInterval(() => {
      refreshJobs()
    }, 4000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs])

  /** 执行一个自动流程操作并刷新 */
  const runJobAction = async (key: string, label: string, fn: () => Promise<{ message?: string }>) => {
    setBusyId(key)
    try {
      const result = await fn()
      addLog('info', result.message || `${label}成功`)
      await refreshJobs()
    } catch (error) {
      addLog('error', `${label}失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      {/* 自动流程任务列表 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">任务队列（{jobs.length}）</h2>
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={refreshJobs}>
            <RefreshCw className="size-3.5" /> 刷新
          </Button>
        </div>
        {jobs.length === 0 ? (
          <EmptyHint icon={Inbox} text="暂无自动处理任务，去工作台开始一个吧" />
        ) : (
          jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              busyId={busyId}
              onPause={() => runJobAction(`pause:${job.id}`, '暂停', () => automationApi.pause(job.id))}
              onCancel={() => setConfirm({ title: '取消该任务？', description: `「${job.title}」正在运行的下载或处理进程会被停止。`, action: () => runJobAction(`cancel:${job.id}`, '取消', () => automationApi.cancel(job.id)) })}
              onRetry={() => runJobAction(`retry:${job.id}`, '重试', () => automationApi.retry(job.id))}
              onResume={() => runJobAction(`resume:${job.id}`, '继续', () => automationApi.resume(job.id))}
              onSkip={() => setConfirm({ title: '跳过当前画面处理？', description: `「${job.title}」正在运行的 ffmpeg 会被停止，后续直接使用下载的原视频。`, action: () => runJobAction(`skip:${job.id}`, '跳过当前阶段', () => automationApi.skipCurrentStage(job.id)) })}
              onOpenSubtitle={() => openSubtitleWorkbench(job.id)}
            />
          ))
        )}
      </section>

      {/* 统一确认对话框 */}
      <AlertDialog open={!!confirm} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent className="glass-strong">
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirm?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => { confirm?.action(); setConfirm(null) }}>确认</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** 单个自动流程任务卡 */
function JobCard({
  job, busyId, onPause, onCancel, onRetry, onResume, onSkip, onOpenSubtitle,
}: {
  job: AutomationJob
  busyId: string | null
  onPause: () => void
  onCancel: () => void
  onRetry: () => void
  onResume: () => void
  onSkip: () => void
  onOpenSubtitle: () => void
}) {
  const meta = STATUS_META[job.status] ?? STATUS_META.pending
  const isEffectsRunning = job.steps.find((s) => s.key === 'effects')?.status === 'running'
  const busy = Boolean(busyId && busyId.endsWith(job.id))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-sm">
          <span className="truncate">{job.title}</span>
          <Badge variant={meta.variant}>{meta.label}</Badge>
        </CardTitle>
        <Progress value={job.progress} className="mt-2" />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {job.steps.map((step) => (
            <span key={step.key} className={cn('rounded-md border px-2 py-0.5 text-[11px]', (STATUS_META[step.status] ?? STATUS_META.pending).tone)}>
              {step.label}
            </span>
          ))}
        </div>
        {/* 失败或跳过的阶段显示具体原因，便于判断是否需要单独续跑 */}
        {job.steps.filter((s) => (s.status === 'failed' || s.status === 'skipped') && s.error_message).map((s) => (
          <p key={s.key} className={cn('text-xs', s.status === 'failed' ? 'text-destructive' : 'text-warning')}>
            {s.label}：{s.error_message}
          </p>
        ))}
        <Separator />
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={onOpenSubtitle}>
            字幕调整
          </Button>
          {job.can_pause && (
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onPause}><Pause className="size-3.5" /> 暂停</Button>
          )}
          {job.can_pause && isEffectsRunning && (
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onSkip}><SkipForward className="size-3.5" /> 跳过画面</Button>
          )}
          {job.can_resume && (
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onResume}>
              <Play className="size-3.5" /> {job.status === 'paused' ? '继续' : '断点续跑'}
            </Button>
          )}
          {job.can_retry && (
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onRetry}><RotateCcw className="size-3.5" /> 全部重跑</Button>
          )}
          {job.can_cancel && (
            <Button variant="outline" size="sm" className="gap-1.5 text-destructive" disabled={busy} onClick={onCancel}><X className="size-3.5" /> 取消</Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** 空状态提示 */
function EmptyHint({ icon: Icon, text }: { icon: typeof Inbox; text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-10 text-center">
      <Icon className="size-7 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">{text}</p>
    </div>
  )
}
