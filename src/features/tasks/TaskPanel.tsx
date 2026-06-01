// src/features/tasks/TaskPanel.tsx
// 任务面板组件 - 显示一键自动流程、底层任务记录和当前视频信息

import { useEffect, useMemo, useState } from 'react'
import { automationApi, taskApi } from '@/lib/api'
import { buildAutomationPayload } from '@/lib/automationPayload'
import { useTaskStore } from '@/stores/taskStore'
import type { AutomationJob, AutomationStep, BackendAutomationJob, DownloadTask } from '@/types'
import { VideoInfoCard } from './VideoInfoCard'

/**
 * 任务面板
 * 显示当前解析的视频信息、一键自动处理流程和底层任务记录。
 */
export function TaskPanel() {
  const { currentVideo, tasks, automationJobs, addLog, upsertAutomationJob, removeTask, clearTasks } = useTaskStore()
  const [serverTasks, setServerTasks] = useState<DownloadTask[]>([])
  const [isLoadingTasks, setIsLoadingTasks] = useState(false)
  const [deletingTaskId, setDeletingTaskId] = useState<number | null>(null)
  const [isClearingTasks, setIsClearingTasks] = useState(false)
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null)
  const [resumingJobId, setResumingJobId] = useState<string | null>(null)
  const [controllingJob, setControllingJob] = useState<string | null>(null)
  const [controllingTask, setControllingTask] = useState<string | null>(null)
  const [batchUrls, setBatchUrls] = useState('')
  const [batchConcurrency, setBatchConcurrency] = useState(2)
  const [isStartingBatch, setIsStartingBatch] = useState(false)
  const [controllingBatchId, setControllingBatchId] = useState<string | null>(null)

  const mergedTasks = useMemo(() => {
    const taskMap = new Map<number, DownloadTask>()
    for (const task of tasks) taskMap.set(task.id, task)
    for (const task of serverTasks) taskMap.set(task.id, task)
    return Array.from(taskMap.values()).sort((a, b) => b.id - a.id)
  }, [serverTasks, tasks])

  const activeJobIds = useMemo(
    () => automationJobs
      .filter((job) => job.status === 'running' || job.status === 'pending')
      .map((job) => job.id)
      .sort()
      .join('|'),
    [automationJobs],
  )

  const batchSummaries = useMemo(() => collectBatchSummaries(automationJobs), [automationJobs])

  /** 从后端读取持久化任务记录 */
  const loadTasks = async () => {
    setIsLoadingTasks(true)
    try {
      const [taskData, automationData] = await Promise.all([
        taskApi.list(),
        automationApi.listJobs(),
      ])
      setServerTasks(taskData)
      automationData.forEach((job) => upsertAutomationJob(mapBackendAutomationJob(job)))
    } catch (error) {
      addLog('error', `加载任务列表失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsLoadingTasks(false)
    }
  }

  useEffect(() => {
    loadTasks()
  }, [])

  /** 重试失败或已完成的自动化任务 */
  const retryAutomationJob = async (jobId: string) => {
    setRetryingJobId(jobId)
    try {
      const result = await automationApi.retry(jobId)
      addLog('info', `自动处理任务已重新进入队列: ${result.job_id}`)
      await loadTasks()
    } catch (error) {
      addLog('error', `重试自动处理失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setRetryingJobId(null)
    }
  }

  /** 从已完成阶段继续自动化任务 */
  const resumeAutomationJob = async (jobId: string) => {
    setResumingJobId(jobId)
    try {
      const result = await automationApi.resume(jobId)
      addLog('info', `自动处理任务已从断点继续: ${result.job_id}`)
      await loadTasks()
    } catch (error) {
      addLog('error', `继续自动处理失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setResumingJobId(null)
    }
  }

  /** 暂停或取消单个自动化任务 */
  const controlAutomationJob = async (job: AutomationJob, action: 'pause' | 'cancel') => {
    if (action === 'cancel' && !window.confirm(`确认取消自动处理任务「${job.title}」？正在运行的下载或画面处理进程会被停止。`)) return

    setControllingJob(`${action}:${job.id}`)
    try {
      const result = action === 'pause'
        ? await automationApi.pause(job.id)
        : await automationApi.cancel(job.id)
      addLog(action === 'pause' ? 'warn' : 'error', result.message)
      await loadTasks()
    } catch (error) {
      addLog('error', `${action === 'pause' ? '暂停' : '取消'}自动处理失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setControllingJob(null)
    }
  }

  /** 按行批量提交自动化任务 */
  const startBatchAutomation = async () => {
    const urls = batchUrls
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
    if (urls.length === 0 || isStartingBatch) return

    setIsStartingBatch(true)
    try {
      const result = await automationApi.startBatch({
        urls,
        concurrency: batchConcurrency,
        template: buildAutomationPayload(urls[0]),
      })
      addLog('info', `批量自动处理已入队: ${result.accepted_count} 个任务，批次 ${result.batch_id}`)
      setBatchUrls('')
      await loadTasks()
    } catch (error) {
      addLog('error', `批量自动处理失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsStartingBatch(false)
    }
  }

  /** 暂停或恢复一个批次 */
  const controlBatch = async (batchId: string, action: 'pause' | 'resume') => {
    setControllingBatchId(batchId)
    try {
      const result = action === 'pause'
        ? await automationApi.pauseBatch(batchId)
        : await automationApi.resumeBatch(batchId)
      addLog('info', `${result.message}: ${result.affected_count} 个任务`)
      await loadTasks()
    } catch (error) {
      addLog('error', `${action === 'pause' ? '暂停' : '恢复'}批量任务失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setControllingBatchId(null)
    }
  }

  /** 删除单条底层任务记录 */
  const deleteTaskRecord = async (task: DownloadTask) => {
    setDeletingTaskId(task.id)
    try {
      await taskApi.delete(task.id)
      setServerTasks((current) => current.filter((item) => item.id !== task.id))
      removeTask(task.id)
      addLog('info', `任务记录已删除: #${task.id}`)
    } catch (error) {
      addLog('error', `删除任务记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setDeletingTaskId(null)
    }
  }

  /** 控制单条底层任务，优先停止真实外部进程 */
  const controlTaskRecord = async (task: DownloadTask, action: 'pause' | 'cancel' | 'retry') => {
    if (action === 'cancel' && !window.confirm(`确认取消任务 #${task.id}？正在运行的外部进程会被停止。`)) return

    setControllingTask(`${action}:${task.id}`)
    try {
      if (action === 'pause') {
        const result = await taskApi.pause(task.id)
        addLog('warn', result.message)
      } else if (action === 'cancel') {
        const result = await taskApi.cancel(task.id)
        addLog('error', result.message)
      } else {
        const result = await taskApi.retry(task.id)
        addLog('info', result.message)
      }
      await loadTasks()
    } catch (error) {
      const actionLabel = action === 'pause' ? '暂停' : action === 'cancel' ? '取消' : '重试'
      addLog('error', `${actionLabel}任务失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setControllingTask(null)
    }
  }

  /** 批量清理已结束底层任务 */
  const clearTaskRecords = async (status?: DownloadTask['status']) => {
    setIsClearingTasks(true)
    try {
      const result = await taskApi.clear(status)
      clearTasks(status)
      addLog('info', `已清理 ${result.deleted_count} 条任务记录`)
      await loadTasks()
    } catch (error) {
      addLog('error', `清理任务记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsClearingTasks(false)
    }
  }

  /** 把异常遗留的执行中任务标记失败，便于用户删除或重试 */
  const cleanupInterruptedTasks = async () => {
    const confirmed = window.confirm('确认将所有显示为下载中/处理中的底层任务标记为失败？这适合清理已经卡住的记录，正常运行中的任务不要使用。')
    if (!confirmed) return
    setIsClearingTasks(true)
    try {
      const result = await taskApi.cleanupInterrupted()
      addLog('warn', `已处理 ${result.updated_count} 条卡住的执行中任务`)
      await loadTasks()
    } catch (error) {
      addLog('error', `清理卡住任务失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsClearingTasks(false)
    }
  }

  useEffect(() => {
    if (!activeJobIds) return

    const sources = activeJobIds.split('|').map((jobId) => {
      const source = new EventSource(automationApi.eventsUrl(jobId))
      source.addEventListener('job', (event) => {
        upsertAutomationJob(mapBackendAutomationJob(JSON.parse((event as MessageEvent).data) as BackendAutomationJob))
      })
      source.addEventListener('error', () => {
        source.close()
      })
      return source
    })

    const timer = window.setInterval(loadTasks, 4000)
    return () => {
      sources.forEach((source) => source.close())
      window.clearInterval(timer)
    }
  }, [activeJobIds])

  return (
    <div className="min-h-full p-4">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-4">
        {currentVideo && <VideoInfoCard video={currentVideo} />}

        <section className="rounded-lg border border-border bg-background-elevated p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">批量自动处理</h3>
              <p className="mt-1 text-xs text-foreground-muted">每行一个 YouTube 链接，使用当前画面处理配置入队执行。</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-foreground-muted">
              <span>并发</span>
              <input
                type="number"
                min={1}
                max={8}
                value={batchConcurrency}
                onChange={(event) => setBatchConcurrency(Math.min(8, Math.max(1, Number(event.target.value) || 1)))}
                className="h-8 w-16 rounded-md border border-border bg-background px-2 text-sm text-foreground"
              />
              <button
                onClick={startBatchAutomation}
                disabled={!batchUrls.trim() || isStartingBatch}
                className="h-8 rounded-md bg-accent px-4 text-sm text-accent-foreground hover:bg-accent/90 disabled:opacity-50"
              >
                {isStartingBatch ? '入队中...' : '批量入队'}
              </button>
            </div>
          </div>
          <textarea
            value={batchUrls}
            onChange={(event) => setBatchUrls(event.target.value)}
            placeholder="每行一个链接..."
            className="min-h-28 w-full resize-y rounded-md border border-border bg-background p-3 text-sm text-foreground placeholder:text-foreground-muted focus:border-primary focus:outline-none"
          />
        </section>

        <section className="rounded-lg border border-border bg-background-elevated p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">自动处理队列</h3>
              <p className="mt-1 text-xs text-foreground-muted">一键完成会按解析、下载、画面处理、字幕、配音、导出阶段推进。</p>
            </div>
            <StatusSummary jobs={automationJobs} />
          </div>

          {batchSummaries.length > 0 && (
            <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-3">
              {batchSummaries.map((batch) => (
                <BatchSummaryCard
                  key={batch.batchId}
                  batch={batch}
                  isBusy={controllingBatchId === batch.batchId}
                  onPause={() => controlBatch(batch.batchId, 'pause')}
                  onResume={() => controlBatch(batch.batchId, 'resume')}
                />
              ))}
            </div>
          )}

          {automationJobs.length === 0 ? (
            <EmptyAutomationList />
          ) : (
            <div className="space-y-3">
              {automationJobs.map((job) => (
                <AutomationJobCard
                  key={job.id}
                  job={job}
                  isRetrying={retryingJobId === job.id}
                  isResuming={resumingJobId === job.id}
                  controllingAction={controllingJob?.endsWith(`:${job.id}`) ? controllingJob.split(':')[0] as 'pause' | 'cancel' : null}
                  onRetry={() => retryAutomationJob(job.id)}
                  onResume={() => resumeAutomationJob(job.id)}
                  onPause={() => controlAutomationJob(job, 'pause')}
                  onCancel={() => controlAutomationJob(job, 'cancel')}
                />
              ))}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border bg-background-elevated p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">底层任务记录</h3>
              <p className="mt-1 text-xs text-foreground-muted">显示下载、画面处理、字幕、配音和导出任务的实际执行记录。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={loadTasks}
                disabled={isLoadingTasks}
                className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50"
              >
                {isLoadingTasks ? '刷新中...' : '刷新'}
              </button>
              <button
                onClick={() => clearTaskRecords('failed')}
                disabled={isClearingTasks}
                className="h-9 rounded-md border border-destructive/40 px-4 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-50"
              >
                清理失败
              </button>
              <button
                onClick={cleanupInterruptedTasks}
                disabled={isClearingTasks}
                className="h-9 rounded-md border border-warning/40 px-4 text-sm text-warning hover:bg-warning/10 disabled:opacity-50"
              >
                标记中断
              </button>
              <button
                onClick={() => clearTaskRecords()}
                disabled={isClearingTasks}
                className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50"
              >
                清理已结束
              </button>
            </div>
          </div>

          {mergedTasks.length === 0 ? (
            <EmptyTaskList />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-3">
              {mergedTasks.map((task) => (
                <TaskItem
                  key={task.id}
                  task={task}
                  isDeleting={deletingTaskId === task.id}
                  busyAction={controllingTask?.endsWith(`:${task.id}`) ? controllingTask.split(':')[0] as 'pause' | 'cancel' | 'retry' : null}
                  onPause={() => controlTaskRecord(task, 'pause')}
                  onCancel={() => controlTaskRecord(task, 'cancel')}
                  onRetry={() => controlTaskRecord(task, 'retry')}
                  onDelete={() => deleteTaskRecord(task)}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

/** 把后端持久化自动任务转换为前端展示结构 */
function mapBackendAutomationJob(job: BackendAutomationJob): AutomationJob {
  const stageLabels: Record<AutomationStep['key'], string> = {
    parse: '解析视频',
    download: '下载入库',
    effects: '画面处理',
    subtitle: '字幕处理',
    voice: '配音生成',
    export: '合成导出',
  }
  const stageDescriptions: Record<AutomationStep['key'], string> = {
    parse: '读取 YouTube 元数据和字幕轨',
    download: '下载原视频并归档到项目目录',
    effects: '应用画面差异化和输出参数',
    subtitle: '生成、翻译、润色并渲染字幕',
    voice: '按配置生成或跳过配音',
    export: '合成视频、字幕、配音并导出成品',
  }
  const steps = (Object.keys(stageLabels) as AutomationStep['key'][]).map((key) => {
    const stage = job.stages.find((item) => item.key === key)
    const isCurrentStep = job.status === 'running' && Boolean(job.current_step?.includes({
      parse: '解析',
      download: '下载',
      effects: '画面',
      subtitle: '字幕',
      voice: '配音',
      export: '导出',
    }[key]))
    return {
      key,
      label: stageLabels[key],
      description: stageDescriptions[key],
      status: stage?.status === 'completed' || stage?.status === 'failed' || stage?.status === 'skipped'
        ? stage.status
        : stage?.status === 'paused' || stage?.status === 'cancelled'
          ? stage.status
        : isCurrentStep || stage?.status === 'running'
          ? 'running'
          : 'pending',
      progress: stage?.status === 'completed' || stage?.status === 'skipped' ? 100 : Math.round(stage?.progress || 0),
      output_path: stage?.output_path || null,
      error_message: stage?.error_message || null,
    }
  })

  return {
    id: job.id,
    title: job.title || '一键自动流程',
    source_url: job.source_url,
    video_id: job.video_id,
    status: job.status,
    progress: Math.round(job.progress || 0),
    current_step: job.current_step || '等待开始',
    batch_id: job.batch_id,
    created_at: job.created_at || new Date().toISOString(),
    completed_at: job.completed_at,
    steps,
    can_pause: job.can_pause,
    can_cancel: job.can_cancel,
    can_resume: job.can_resume,
    can_retry: job.can_retry,
  }
}

/** 自动流程概览 */
function StatusSummary({ jobs }: { jobs: AutomationJob[] }) {
  const runningCount = jobs.filter((job) => job.status === 'running').length
  const pausedCount = jobs.filter((job) => job.status === 'paused').length
  const failedCount = jobs.filter((job) => job.status === 'failed').length
  const cancelledCount = jobs.filter((job) => job.status === 'cancelled').length

  return (
    <div className="flex flex-wrap gap-2 text-xs">
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-foreground-muted">全部 {jobs.length}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-accent">执行中 {runningCount}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-warning">暂停 {pausedCount}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-destructive">失败 {failedCount}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-foreground-muted">已取消 {cancelledCount}</span>
    </div>
  )
}

type BatchSummary = {
  batchId: string
  total: number
  running: number
  pending: number
  paused: number
  cancelled: number
  completed: number
  failed: number
  progress: number
}

/** 按批次聚合自动任务，用于批量暂停和恢复 */
function collectBatchSummaries(jobs: AutomationJob[]): BatchSummary[] {
  const map = new Map<string, BatchSummary>()
  for (const job of jobs) {
    if (!job.batch_id) continue
    const summary = map.get(job.batch_id) || {
      batchId: job.batch_id,
      total: 0,
      running: 0,
      pending: 0,
      paused: 0,
      cancelled: 0,
      completed: 0,
      failed: 0,
      progress: 0,
    }
    summary.total += 1
    summary.progress += job.progress || 0
    summary[job.status] += 1
    map.set(job.batch_id, summary)
  }
  return Array.from(map.values()).map((summary) => ({
    ...summary,
    progress: summary.total ? Math.round(summary.progress / summary.total) : 0,
  }))
}

/** 批量任务控制卡片 */
function BatchSummaryCard({
  batch,
  isBusy,
  onPause,
  onResume,
}: {
  batch: BatchSummary
  isBusy: boolean
  onPause: () => void
  onResume: () => void
}) {
  const canPause = batch.pending + batch.running > 0 && batch.paused === 0
  const canResume = batch.paused > 0

  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">批次 {batch.batchId}</p>
          <p className="mt-1 text-xs text-foreground-muted">
            共 {batch.total} 个，完成 {batch.completed}，执行 {batch.running}，等待 {batch.pending}，暂停 {batch.paused}，取消 {batch.cancelled}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={onPause}
            disabled={!canPause || isBusy}
            className="h-8 rounded-md border border-warning/40 px-3 text-xs text-warning hover:bg-warning/10 disabled:opacity-50"
          >
            {isBusy && canPause ? '处理中...' : '暂停'}
          </button>
          <button
            onClick={onResume}
            disabled={!canResume || isBusy}
            className="h-8 rounded-md border border-accent/40 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {isBusy && canResume ? '处理中...' : '恢复'}
          </button>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-background-elevated">
        <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${batch.progress}%` }} />
      </div>
    </div>
  )
}

/** 空自动流程列表 */
function EmptyAutomationList() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-background p-6 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg border border-border text-foreground-muted">
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h6M4 12h10M4 18h16m-5-6l3 3 3-3" />
        </svg>
      </div>
      <p className="text-sm font-medium">暂无自动处理流程</p>
      <p className="mt-1 text-xs text-foreground-muted">顶部输入链接后点击一键完成，阶段进度会显示在这里。</p>
    </div>
  )
}

/** 自动流程卡片 */
function AutomationJobCard({
  job,
  isRetrying,
  isResuming,
  controllingAction,
  onRetry,
  onResume,
  onPause,
  onCancel,
}: {
  job: AutomationJob
  isRetrying: boolean
  isResuming: boolean
  controllingAction: 'pause' | 'cancel' | null
  onRetry: () => void
  onResume: () => void
  onPause: () => void
  onCancel: () => void
}) {
  const canPause = job.can_pause ?? (job.status === 'pending' || job.status === 'running')
  const canCancel = job.can_cancel ?? (job.status === 'pending' || job.status === 'running' || job.status === 'paused')
  const canResume = job.can_resume ?? (job.status === 'paused' || job.status === 'failed' || job.status === 'cancelled' || job.status === 'completed')
  const canRetry = job.can_retry ?? (job.status === 'failed' || job.status === 'cancelled' || job.status === 'completed')

  return (
    <article className="rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="max-w-[560px] truncate text-sm font-medium">{job.title}</h4>
            <StatusBadge status={job.status} />
          </div>
          <p className="mt-1 max-w-3xl truncate text-xs text-foreground-muted">{job.source_url}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2 text-right text-xs text-foreground-muted">
          <div>
            <div>{formatTime(job.created_at)}</div>
            <div className="mt-1">{job.current_step}</div>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {canPause && (
              <button
                onClick={onPause}
                disabled={controllingAction !== null}
                className="h-9 min-w-16 rounded-md border border-warning/40 px-3 text-xs text-warning hover:bg-warning/10 disabled:opacity-50"
              >
                {controllingAction === 'pause' ? '暂停中...' : '暂停'}
              </button>
            )}
            {canCancel && (
              <button
                onClick={onCancel}
                disabled={controllingAction !== null}
                className="h-9 min-w-16 rounded-md border border-destructive/40 px-3 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
              >
                {controllingAction === 'cancel' ? '取消中...' : '取消'}
              </button>
            )}
            {canResume && (
              <button
                onClick={onResume}
                disabled={isResuming}
                className="h-9 min-w-16 rounded-md border border-accent/40 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
              >
                {isResuming ? '继续中...' : '继续'}
              </button>
            )}
            {canRetry && (
              <button
                onClick={onRetry}
                disabled={isRetrying}
                className="h-9 min-w-16 rounded-md border border-border px-3 text-xs text-foreground hover:bg-white/5 disabled:opacity-50"
              >
                {isRetrying ? '重试中...' : '重试'}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs text-foreground-muted">
          <span>总进度</span>
          <span>{job.progress}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-background-elevated">
          <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${job.progress}%` }} />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-2">
        {job.steps.map((step) => (
          <AutomationStepItem key={step.key} step={step} />
        ))}
      </div>
    </article>
  )
}

/** 自动流程阶段项 */
function AutomationStepItem({ step }: { step: AutomationStep }) {
  return (
    <div className="rounded-md border border-border bg-background-elevated p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium">{step.label}</span>
        <StepStatus status={step.status} />
      </div>
      <p className="mt-1 line-clamp-2 text-[11px] text-foreground-muted">{step.error_message || step.description}</p>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-background">
        <div className={`h-full rounded-full transition-all duration-300 ${progressColor(step.status)}`} style={{ width: `${step.progress}%` }} />
      </div>
    </div>
  )
}

/** 空任务列表 */
function EmptyTaskList() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-background p-6 text-center text-foreground-muted">
      <svg className="mx-auto mb-3 h-10 w-10 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
      <p className="text-sm">暂无底层任务记录</p>
    </div>
  )
}

/** 任务项 */
function TaskItem({
  task,
  isDeleting,
  busyAction,
  onPause,
  onCancel,
  onRetry,
  onDelete,
}: {
  task: DownloadTask
  isDeleting: boolean
  busyAction: 'pause' | 'cancel' | 'retry' | null
  onPause: () => void
  onCancel: () => void
  onRetry: () => void
  onDelete: () => void
}) {
  const canPause = task.can_pause ?? (task.status === 'pending' || task.status === 'processing' || task.status === 'downloading')
  const canCancel = task.can_cancel ?? (task.status === 'pending' || task.status === 'processing' || task.status === 'downloading' || task.status === 'paused')
  const canRetry = task.can_retry ?? (task.status === 'failed' || task.status === 'cancelled' || task.status === 'paused')
  const canDelete = task.can_delete ?? !(task.status === 'processing' || task.status === 'downloading')

  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{taskTypeText(task.task_type)}</p>
          <p className="mt-1 text-xs text-foreground-muted">任务 #{task.id}</p>
        </div>
        <div className="shrink-0">
          <TaskStatus status={task.status} />
        </div>
      </div>

      <div className="h-1.5 overflow-hidden rounded-full bg-background-elevated">
        <div
          className={`h-full transition-all duration-300 ${taskProgressColor(task.status)}`}
          style={{ width: `${Math.min(100, Math.max(0, task.progress || 0))}%` }}
        />
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-foreground-muted">
        <span>{Math.round(task.progress || 0)}%</span>
        {task.output_path && <span className="max-w-[180px] truncate select-text">{task.output_path}</span>}
      </div>
      {task.error_message && <p className="mt-2 text-xs text-destructive">{task.error_message}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {canPause && (
          <button
            onClick={onPause}
            disabled={busyAction !== null}
            className="h-9 min-w-16 rounded-md border border-warning/40 px-3 text-xs text-warning hover:bg-warning/10 disabled:opacity-50"
          >
            {busyAction === 'pause' ? '暂停中...' : '暂停'}
          </button>
        )}
        {canCancel && (
          <button
            onClick={onCancel}
            disabled={busyAction !== null}
            className="h-9 min-w-16 rounded-md border border-destructive/40 px-3 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            {busyAction === 'cancel' ? '取消中...' : '取消'}
          </button>
        )}
        {canRetry && (
          <button
            onClick={onRetry}
            disabled={busyAction !== null}
            className="h-9 min-w-16 rounded-md border border-accent/40 px-3 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {busyAction === 'retry' ? '重试中...' : '重试'}
          </button>
        )}
        {canDelete && (
          <button
            onClick={onDelete}
            disabled={isDeleting}
            className="h-9 min-w-16 rounded-md border border-border px-3 text-xs text-foreground-muted hover:border-destructive/40 hover:text-destructive disabled:opacity-50"
          >
            {isDeleting ? '删除中...' : '删除'}
          </button>
        )}
      </div>
    </div>
  )
}

/** 自动任务状态徽标 */
function StatusBadge({ status }: { status: AutomationJob['status'] }) {
  const labels: Record<AutomationJob['status'], string> = {
    pending: '等待中',
    running: '执行中',
    paused: '已暂停',
    cancelled: '已取消',
    completed: '已完成',
    failed: '失败',
  }
  const classes: Record<AutomationJob['status'], string> = {
    pending: 'border-border text-foreground-muted',
    running: 'border-accent/40 text-accent',
    paused: 'border-warning/40 text-warning',
    cancelled: 'border-border text-foreground-muted',
    completed: 'border-success/40 text-success',
    failed: 'border-destructive/40 text-destructive',
  }
  return <span className={`rounded-md border px-2 py-0.5 text-xs ${classes[status]}`}>{labels[status]}</span>
}

/** 自动流程阶段状态 */
function StepStatus({ status }: { status: AutomationStep['status'] }) {
  const labels: Record<AutomationStep['status'], string> = {
    pending: '等待',
    running: '进行中',
    paused: '暂停',
    cancelled: '取消',
    completed: '完成',
    failed: '失败',
    skipped: '跳过',
  }
  const classes: Record<AutomationStep['status'], string> = {
    pending: 'bg-foreground-muted',
    running: 'bg-accent',
    paused: 'bg-warning',
    cancelled: 'bg-foreground-muted',
    completed: 'bg-success',
    failed: 'bg-destructive',
    skipped: 'bg-warning',
  }
  return (
    <span className="flex shrink-0 items-center gap-1 text-[10px] text-foreground-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${classes[status]}`} />
      {labels[status]}
    </span>
  )
}

/** 底层任务状态 */
function TaskStatus({ status }: { status: DownloadTask['status'] }) {
  const labels: Record<DownloadTask['status'], string> = {
    pending: '等待中',
    downloading: '下载中',
    processing: '处理中',
    paused: '已暂停',
    cancelled: '已取消',
    completed: '已完成',
    failed: '失败',
  }
  const classes: Record<DownloadTask['status'], string> = {
    pending: 'text-foreground-muted',
    downloading: 'text-accent',
    processing: 'text-warning',
    paused: 'text-warning',
    cancelled: 'text-foreground-muted',
    completed: 'text-success',
    failed: 'text-destructive',
  }
  return <span className={`text-xs ${classes[status]}`}>{labels[status]}</span>
}

/** 自动流程阶段进度颜色 */
function progressColor(status: AutomationStep['status']) {
  if (status === 'failed') return 'bg-destructive'
  if (status === 'paused' || status === 'skipped') return 'bg-warning'
  if (status === 'cancelled') return 'bg-foreground-muted'
  if (status === 'completed') return 'bg-success'
  return 'bg-accent'
}

/** 底层任务进度颜色 */
function taskProgressColor(status: DownloadTask['status']) {
  if (status === 'failed') return 'bg-destructive'
  if (status === 'paused') return 'bg-warning'
  if (status === 'cancelled') return 'bg-foreground-muted'
  if (status === 'completed') return 'bg-success'
  return 'bg-accent'
}

/** 任务类型显示文本 */
function taskTypeText(type: DownloadTask['task_type']) {
  const texts: Record<DownloadTask['task_type'], string> = {
    download: '视频下载',
    effects: '画面处理',
    subtitle: '字幕处理',
    voice: '配音生成',
    export: '视频导出',
  }
  return texts[type] || type
}

/** 格式化时间 */
function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
