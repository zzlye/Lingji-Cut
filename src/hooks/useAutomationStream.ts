// src/hooks/useAutomationStream.ts
// 全局自动化进度流 - 在 AppShell 顶层挂载一次，作为唯一的 SSE 订阅入口，
// 取代原先 Header 与 TaskPanel 各开一份 EventSource 的重复监听。
import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { automationApi } from '@/lib/api'
import { useAutomationStore, selectActiveJobIds } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import type { BackendAutomationJob } from '@/types'

const ACTIVE_JOB_POLL_INTERVAL_MS = 4000

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
  // 记录已提示完成的任务，避免重复 Toast
  const notifiedRef = useRef<Set<string>>(new Set())

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

  // 为每个活跃任务建立 SSE 连接
  useEffect(() => {
    if (typeof EventSource === 'undefined' || activeIds.length === 0) return

    const syncBackendJob = useAutomationStore.getState().syncBackendJob
    const addLog = useLogStore.getState().addLog
    const sources = activeIds.map((id) => {
      const source = new EventSource(automationApi.eventsUrl(id))
      source.addEventListener('job', (event) => {
        const job = JSON.parse((event as MessageEvent).data) as BackendAutomationJob
        syncBackendJob(job)
        if (job.status === 'completed' && !notifiedRef.current.has(job.id)) {
          notifiedRef.current.add(job.id)
          addLog('info', `一键流程完成: ${job.output_path || '已导出'}`)
          toast.success('视频已导出完成')
          source.close()
        } else if (job.status === 'failed' && !notifiedRef.current.has(job.id)) {
          notifiedRef.current.add(job.id)
          addLog('error', `一键流程失败: ${job.error_message || '未知错误'}`)
          source.close()
        } else if (job.status === 'cancelled' || job.status === 'paused') {
          if (!notifiedRef.current.has(job.id)) {
            notifiedRef.current.add(job.id)
            const message = job.error_message || (job.status === 'paused' ? '任务已暂停' : '任务已取消')
            addLog(job.status === 'paused' ? 'info' : 'warn', `一键流程${job.status === 'paused' ? '已暂停' : '已中断'}: ${message}`)
            if (job.status === 'cancelled') toast.warning('任务已中断，可在任务队列点击断点续跑')
          }
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
