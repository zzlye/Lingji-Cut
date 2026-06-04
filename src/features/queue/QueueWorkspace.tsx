// src/features/queue/QueueWorkspace.tsx
// 任务队列工作区 - 自动流程任务、批量入队、底层任务记录
// 进度由全局 useAutomationStream(SSE) 维护，这里只轮询底层任务记录，不再重复开 SSE
import { useEffect, useMemo, useState } from 'react'
import {
  Play, Pause, X, RotateCcw, SkipForward, Trash2, RefreshCw, Layers, Inbox, Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import { automationApi, taskApi } from '@/lib/api'
import { buildAutomationPayload } from '@/lib/automationPayload'
import { collectBatchSummaries } from '@/lib/automationMapper'
import { useAutomationStore } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import type { AutomationJob, AutomationStep, DownloadTask } from '@/types'

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

/** 底层任务类型中文名 */
const TASK_TYPE_LABEL: Record<DownloadTask['task_type'], string> = {
  download: '下载', effects: '画面处理', subtitle: '字幕', voice: '配音', export: '导出',
}

/** 待确认操作 */
type PendingConfirm = { title: string; description: string; action: () => void }

export function QueueWorkspace() {
  const jobs = useAutomationStore((s) => s.jobs)
  const syncBackendJobs = useAutomationStore((s) => s.syncBackendJobs)
  const addLog = useLogStore((s) => s.addLog)

  const [serverTasks, setServerTasks] = useState<DownloadTask[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [batchUrls, setBatchUrls] = useState('')
  const [batchConcurrency, setBatchConcurrency] = useState(2)
  const [isStartingBatch, setIsStartingBatch] = useState(false)
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null)

  const batchSummaries = useMemo(() => collectBatchSummaries(jobs), [jobs])
  const hasActiveTasks = useMemo(
    () => serverTasks.some((t) => t.status === 'processing' || t.status === 'downloading' || t.status === 'pending'),
    [serverTasks],
  )

  /** 刷新自动流程任务（兜底，常态由全局 SSE 推送） */
  const refreshJobs = async () => {
    try {
      syncBackendJobs(await automationApi.listJobs())
    } catch {
      // 后端短暂不可用时静默，下一轮再试
    }
  }

  /** 刷新底层任务记录 */
  const refreshTasks = async () => {
    try {
      setServerTasks(await taskApi.list())
    } catch {
      // 同上
    }
  }

  useEffect(() => {
    refreshJobs()
    refreshTasks()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 有活跃任务时按较短间隔轮询底层任务记录（automation 进度由全局 SSE 负责）
  useEffect(() => {
    const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'pending')
    if (!hasActiveJobs && !hasActiveTasks) return
    const timer = window.setInterval(() => {
      refreshTasks()
      if (hasActiveJobs) refreshJobs()
    }, hasActiveTasks ? 1500 : 4000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, hasActiveTasks])

  /** 执行一个自动流程操作并刷新 */
  const runJobAction = async (key: string, label: string, fn: () => Promise<{ message?: string }>) => {
    setBusyId(key)
    try {
      const result = await fn()
      addLog('info', result.message || `${label}成功`)
      await refreshJobs()
      await refreshTasks()
    } catch (error) {
      addLog('error', `${label}失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setBusyId(null)
    }
  }

  /** 批量提交 */
  const startBatch = async () => {
    const urls = batchUrls.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
    if (urls.length === 0 || isStartingBatch) return
    setIsStartingBatch(true)
    try {
      const result = await automationApi.startBatch({ urls, concurrency: batchConcurrency, template: buildAutomationPayload(urls[0]) })
      addLog('info', `批量入队成功：${result.accepted_count} 个任务（批次 ${result.batch_id}）`)
      setBatchUrls('')
      await refreshJobs()
    } catch (error) {
      addLog('error', `批量入队失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsStartingBatch(false)
    }
  }

  /** 底层任务操作 */
  const runTaskAction = async (key: string, label: string, fn: () => Promise<{ message?: string }>) => {
    setBusyId(key)
    try {
      const result = await fn()
      addLog('info', result.message || `${label}成功`)
      await refreshTasks()
    } catch (error) {
      addLog('error', `${label}失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      {/* 批量入队 */}
      <Card className="glass">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm"><Layers className="size-4" /> 批量自动处理</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={batchUrls}
            onChange={(e) => setBatchUrls(e.target.value)}
            placeholder="每行粘贴一个 YouTube 链接…"
            className="min-h-24 resize-y"
          />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              并发数
              <Input
                type="number"
                min={1}
                max={8}
                value={batchConcurrency}
                onChange={(e) => setBatchConcurrency(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
                className="h-8 w-16"
              />
            </label>
            <div className="flex-1" />
            <Button onClick={startBatch} disabled={!batchUrls.trim() || isStartingBatch} className="gap-1.5">
              {isStartingBatch ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              批量入队
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 批次摘要 */}
      {batchSummaries.map((batch) => {
        const canPause = batch.pending + batch.running > 0 && batch.paused === 0
        const canResume = batch.paused > 0
        return (
          <Card key={batch.batchId}>
            <CardContent className="flex flex-wrap items-center gap-3 pt-6">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">批次 {batch.batchId.slice(0, 8)}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  共 {batch.total} · 完成 {batch.completed} · 运行 {batch.running} · 等待 {batch.pending} · 暂停 {batch.paused} · 失败 {batch.failed}
                </p>
              </div>
              <div className="w-28"><Progress value={batch.progress} /></div>
              <Button variant="outline" size="sm" disabled={!canPause || busyId === `bp:${batch.batchId}`} onClick={() => runJobAction(`bp:${batch.batchId}`, '暂停批次', () => automationApi.pauseBatch(batch.batchId))}>暂停</Button>
              <Button variant="outline" size="sm" disabled={!canResume || busyId === `br:${batch.batchId}`} onClick={() => runJobAction(`br:${batch.batchId}`, '恢复批次', () => automationApi.resumeBatch(batch.batchId))}>恢复</Button>
            </CardContent>
          </Card>
        )
      })}

      {/* 自动流程任务列表 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">自动处理队列（{jobs.length}）</h2>
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => { refreshJobs(); refreshTasks() }}>
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
            />
          ))
        )}
      </section>

      {/* 底层任务记录 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">底层任务记录（{serverTasks.length}）</h2>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => runTaskAction('cleanup', '清理卡住任务', () => taskApi.cleanupInterrupted())}>清理卡住</Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirm({ title: '清理已结束任务记录？', description: '将删除所有已完成/失败/取消的底层任务记录。', action: () => runTaskAction('clear', '清理任务', () => taskApi.clear()) })}>清理已结束</Button>
          </div>
        </div>
        {serverTasks.length === 0 ? (
          <EmptyHint icon={Inbox} text="暂无底层任务记录" />
        ) : (
          <div className="space-y-2">
            {serverTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                busyId={busyId}
                onCancel={() => setConfirm({ title: `取消任务 #${task.id}？`, description: '正在运行的外部进程会被停止。', action: () => runTaskAction(`tc:${task.id}`, '取消任务', () => taskApi.cancel(task.id)) })}
                onRetry={() => runTaskAction(`tr:${task.id}`, '重试任务', () => taskApi.retry(task.id))}
                onDelete={() => runTaskAction(`td:${task.id}`, '删除记录', () => taskApi.delete(task.id))}
              />
            ))}
          </div>
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
  job, busyId, onPause, onCancel, onRetry, onResume, onSkip,
}: {
  job: AutomationJob
  busyId: string | null
  onPause: () => void
  onCancel: () => void
  onRetry: () => void
  onResume: () => void
  onSkip: () => void
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

/** 底层任务行 */
function TaskRow({
  task, busyId, onCancel, onRetry, onDelete,
}: {
  task: DownloadTask
  busyId: string | null
  onCancel: () => void
  onRetry: () => void
  onDelete: () => void
}) {
  const meta = STATUS_META[task.status] ?? STATUS_META.pending
  const active = task.status === 'processing' || task.status === 'downloading' || task.status === 'pending'
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
      <Badge variant="outline" className="shrink-0">{TASK_TYPE_LABEL[task.task_type] ?? task.task_type}</Badge>
      <span className="shrink-0 text-xs text-muted-foreground">#{task.id}</span>
      <div className="min-w-0 flex-1">
        {active ? <Progress value={task.progress} /> : <span className="truncate text-xs text-muted-foreground">{task.error_message || task.output_path || '—'}</span>}
      </div>
      <Badge variant={meta.variant} className="shrink-0">{meta.label}</Badge>
      <div className="flex shrink-0 gap-1.5">
        {active && <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={onCancel} aria-label="取消"><X className="size-4" /></Button>}
        {(task.status === 'failed' || task.status === 'cancelled') && <Button variant="ghost" size="icon-sm" onClick={onRetry} aria-label="重试"><RotateCcw className="size-4" /></Button>}
        {!active && <Button variant="ghost" size="icon-sm" className="text-muted-foreground" onClick={onDelete} aria-label="删除"><Trash2 className="size-4" /></Button>}
      </div>
    </div>
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
