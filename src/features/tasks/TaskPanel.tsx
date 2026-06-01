// src/features/tasks/TaskPanel.tsx
// 任务面板组件 - 显示一键自动流程、底层任务记录和当前视频信息

import { useEffect, useMemo, useState } from 'react'
import { automationApi, taskApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import type { AutomationJob, AutomationStep, BackendAutomationJob, DownloadTask } from '@/types'
import { VideoInfoCard } from './VideoInfoCard'

/**
 * 任务面板
 * 显示当前解析的视频信息、一键自动处理流程和底层任务记录。
 */
export function TaskPanel() {
  const { currentVideo, tasks, automationJobs, addLog, upsertAutomationJob } = useTaskStore()
  const [serverTasks, setServerTasks] = useState<DownloadTask[]>([])
  const [isLoadingTasks, setIsLoadingTasks] = useState(false)
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null)

  const mergedTasks = useMemo(() => {
    const taskMap = new Map<number, DownloadTask>()
    for (const task of serverTasks) taskMap.set(task.id, task)
    for (const task of tasks) taskMap.set(task.id, task)
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
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">自动处理队列</h3>
              <p className="mt-1 text-xs text-foreground-muted">一键完成会按解析、下载、画面处理、字幕、配音、导出阶段推进。</p>
            </div>
            <StatusSummary jobs={automationJobs} />
          </div>

          {automationJobs.length === 0 ? (
            <EmptyAutomationList />
          ) : (
            <div className="space-y-3">
              {automationJobs.map((job) => (
                <AutomationJobCard
                  key={job.id}
                  job={job}
                  isRetrying={retryingJobId === job.id}
                  onRetry={() => retryAutomationJob(job.id)}
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
            <button
              onClick={loadTasks}
              disabled={isLoadingTasks}
              className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50"
            >
              {isLoadingTasks ? '刷新中...' : '刷新'}
            </button>
          </div>

          {mergedTasks.length === 0 ? (
            <EmptyTaskList />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-3">
              {mergedTasks.map((task) => (
                <TaskItem key={task.id} task={task} />
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
    created_at: job.created_at || new Date().toISOString(),
    completed_at: job.completed_at,
    steps,
  }
}

/** 自动流程概览 */
function StatusSummary({ jobs }: { jobs: AutomationJob[] }) {
  const runningCount = jobs.filter((job) => job.status === 'running').length
  const failedCount = jobs.filter((job) => job.status === 'failed').length

  return (
    <div className="flex flex-wrap gap-2 text-xs">
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-foreground-muted">全部 {jobs.length}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-accent">执行中 {runningCount}</span>
      <span className="rounded-md border border-border bg-background px-2.5 py-1 text-destructive">失败 {failedCount}</span>
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
function AutomationJobCard({ job, isRetrying, onRetry }: { job: AutomationJob; isRetrying: boolean; onRetry: () => void }) {
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
          {(job.status === 'failed' || job.status === 'completed') && (
            <button
              onClick={onRetry}
              disabled={isRetrying}
              className="h-8 rounded-md border border-border px-3 text-xs text-foreground hover:bg-white/5 disabled:opacity-50"
            >
              {isRetrying ? '重试中...' : '重试'}
            </button>
          )}
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
        <div className={`h-full rounded-full transition-all duration-300 ${step.status === 'failed' ? 'bg-destructive' : 'bg-accent'}`} style={{ width: `${step.progress}%` }} />
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
function TaskItem({ task }: { task: DownloadTask }) {
  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{taskTypeText(task.task_type)}</p>
          <p className="text-xs text-foreground-muted">任务 #{task.id}</p>
        </div>
        <TaskStatus status={task.status} />
      </div>

      <div className="h-1.5 overflow-hidden rounded-full bg-background-elevated">
        <div
          className={`h-full transition-all duration-300 ${task.status === 'failed' ? 'bg-destructive' : 'bg-accent'}`}
          style={{ width: `${Math.min(100, Math.max(0, task.progress || 0))}%` }}
        />
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-foreground-muted">
        <span>{Math.round(task.progress || 0)}%</span>
        {task.output_path && <span className="max-w-[180px] truncate select-text">{task.output_path}</span>}
      </div>
      {task.error_message && <p className="mt-2 text-xs text-destructive">{task.error_message}</p>}
    </div>
  )
}

/** 自动任务状态徽标 */
function StatusBadge({ status }: { status: AutomationJob['status'] }) {
  const labels: Record<AutomationJob['status'], string> = {
    pending: '等待中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
  }
  const classes: Record<AutomationJob['status'], string> = {
    pending: 'border-border text-foreground-muted',
    running: 'border-accent/40 text-accent',
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
    completed: '完成',
    failed: '失败',
    skipped: '跳过',
  }
  const classes: Record<AutomationStep['status'], string> = {
    pending: 'bg-foreground-muted',
    running: 'bg-accent',
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
    completed: '已完成',
    failed: '失败',
  }
  const classes: Record<DownloadTask['status'], string> = {
    pending: 'text-foreground-muted',
    downloading: 'text-accent',
    processing: 'text-warning',
    completed: 'text-success',
    failed: 'text-destructive',
  }
  return <span className={`text-xs ${classes[status]}`}>{labels[status]}</span>
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
