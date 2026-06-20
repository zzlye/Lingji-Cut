// src/hooks/useAutomationStream.ts
// 全局自动化进度流 - 在 AppShell 顶层挂载一次，作为唯一的 SSE 订阅入口，
// 取代原先 Header 与 TaskPanel 各开一份 EventSource 的重复监听。
import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { automationApi } from '@/lib/api'
import { useAutomationStore, selectActiveJobIds } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import type { AutomationJob, AutomationStep, BackendAutomationJob } from '@/types'

const ACTIVE_JOB_POLL_INTERVAL_MS = 4000
const ACTIVE_STATUSES = new Set<AutomationJob['status']>(['pending', 'running'])

/** 后端重启或 SSE 断线后兜底同步完整任务列表，避免界面停在旧进度 */
async function syncAutomationJobs(): Promise<boolean> {
  try {
    const list = await automationApi.listJobs()
    useAutomationStore.getState().syncBackendJobs(list)
    return true
  } catch {
    return false
  }
}

/** 优先同步单个任务，失败时退回完整列表 */
async function syncAutomationJob(id: string): Promise<boolean> {
  try {
    const job = await automationApi.getJob(id)
    useAutomationStore.getState().syncBackendJob(job)
    return true
  } catch {
    return syncAutomationJobs()
  }
}

export function useAutomationStream() {
  const jobs = useAutomationStore((s) => s.jobs)
  const activeIds = selectActiveJobIds(jobs)
  // 用稳定字符串作为依赖，仅在活跃任务集合变化时重建连接
  const activeKey = activeIds.join('|')
  // 记录上一次状态，只在活跃任务进入终态时弹提示，避免刷新页面提示旧任务。
  const previousStatusRef = useRef<Map<string, AutomationJob['status']>>(new Map())
  // 记录已经弹过“继续完成”的失败点，避免 React 严格模式或轮询重复弹窗。
  const resumePromptRef = useRef<Set<string>>(new Set())

  // 挂载时拉取已有任务列表；后端启动慢时重试，避免重启后素材库/历史记录空白。
  useEffect(() => {
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0

    const syncJobs = () => {
      attempts += 1
      syncAutomationJobs()
        .then((ok) => {
          if (!cancelled && ok) {
            attempts = 0
            return
          }
          if (cancelled || attempts >= 20) return
          retryTimer = setTimeout(syncJobs, 1500)
        })
    }

    syncJobs()
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [])

  // SSE 断线、后端自动重启、或 EventSource 没有重连时，用低频轮询兜底同步活跃任务。
  useEffect(() => {
    if (activeIds.length === 0) return
    const timer = window.setInterval(() => {
      void syncAutomationJobs()
    }, ACTIVE_JOB_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey])

  // 统一处理任务完成、失败、暂停和取消提示；SSE 与轮询都只负责同步数据。
  useEffect(() => {
    const previousStatuses = previousStatusRef.current
    const addLog = useLogStore.getState().addLog

    for (const job of jobs) {
      const previousStatus = previousStatuses.get(job.id)
      const justLeftActiveState = previousStatus && ACTIVE_STATUSES.has(previousStatus) && previousStatus !== job.status
      if (!justLeftActiveState) continue

      if (job.status === 'completed') {
        addLog('info', `一键流程完成: ${job.output_path || '已导出'}`)
        toast.success('视频已导出完成')
      } else if (job.status === 'failed') {
        const failedStage = findResumableFailedStage(job)
        const message = failedStage?.error_message || '未知错误'
        addLog('error', `一键流程失败: ${message}`)
        promptResumeFailedStage(job, failedStage, resumePromptRef.current)
      } else if (job.status === 'paused' || job.status === 'cancelled') {
        const failedStage = job.steps.find((step) => step.error_message)
        const message = failedStage?.error_message || (job.status === 'paused' ? '任务已暂停' : '任务已取消')
        addLog(job.status === 'paused' ? 'info' : 'warn', `一键流程${job.status === 'paused' ? '已暂停' : '已中断'}: ${message}`)
        if (job.status === 'cancelled') toast.warning('任务已中断，可在任务队列点击断点续跑')
      }
    }

    previousStatusRef.current = new Map(jobs.map((job) => [job.id, job.status]))
  }, [jobs])

  // 为每个活跃任务建立 SSE 连接
  useEffect(() => {
    if (typeof EventSource === 'undefined' || activeIds.length === 0) return

    const syncBackendJob = useAutomationStore.getState().syncBackendJob
    const sources = activeIds.map((id) => {
      const source = new EventSource(automationApi.eventsUrl(id))
      source.addEventListener('job', (event) => {
        const job = JSON.parse((event as MessageEvent).data) as BackendAutomationJob
        syncBackendJob(job)
        if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled' || job.status === 'paused') {
          source.close()
        }
      })
      source.addEventListener('error', () => {
        // 连接异常时立即拉一次最新状态，避免后端重启后页面一直显示旧的“运行中”。
        void syncAutomationJob(id)
        source.close()
      })
      return source
    })

    return () => sources.forEach((source) => source.close())
  }, [activeKey])
}

/** 找出需要弹继续提示的失败阶段；只处理用户要求的字幕和配音 */
function findResumableFailedStage(job: AutomationJob): AutomationStep | undefined {
  return job.steps.find((step) => (step.key === 'subtitle' || step.key === 'voice') && step.status === 'failed')
}

/** 字幕或配音失败时弹确认框；取消后任务仍保留在队列里，随时能继续 */
function promptResumeFailedStage(job: AutomationJob, failedStage: AutomationStep | undefined, promptedKeys: Set<string>) {
  if (!failedStage || !job.can_resume || typeof window === 'undefined') return
  const promptKey = `${job.id}:${failedStage.key}:${job.completed_at || failedStage.error_message || ''}`
  if (promptedKeys.has(promptKey)) return
  promptedKeys.add(promptKey)

  const message = failedStage.error_message || '重试次数已用完或当前阶段失败'
  const shouldResume = window.confirm(
    `${failedStage.label}失败，任务已停在当前阶段。\n\n${message}\n\n是否现在继续完成？如果还没修好 API、额度或配置，可以点取消，之后在任务队列继续。`,
  )
  if (!shouldResume) return

  const addLog = useLogStore.getState().addLog
  automationApi.resume(job.id)
    .then((result) => {
      addLog('info', result.message || '自动化任务已从断点继续')
      toast.success('已从断点继续处理')
      return syncAutomationJob(job.id)
    })
    .catch((error) => {
      const detail = error instanceof Error ? error.message : '未知错误'
      addLog('error', `继续完成失败: ${detail}`)
      toast.error(`继续完成失败：${detail}`)
    })
}
