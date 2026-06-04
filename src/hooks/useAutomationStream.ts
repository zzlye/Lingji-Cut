// src/hooks/useAutomationStream.ts
// 全局自动化进度流 - 在 AppShell 顶层挂载一次，作为唯一的 SSE 订阅入口，
// 取代原先 Header 与 TaskPanel 各开一份 EventSource 的重复监听。
import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { automationApi } from '@/lib/api'
import { useAutomationStore, selectActiveJobIds } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import type { BackendAutomationJob } from '@/types'

export function useAutomationStream() {
  const jobs = useAutomationStore((s) => s.jobs)
  const activeIds = selectActiveJobIds(jobs)
  // 用稳定字符串作为依赖，仅在活跃任务集合变化时重建连接
  const activeKey = activeIds.join('|')
  // 记录已提示完成的任务，避免重复 Toast
  const notifiedRef = useRef<Set<string>>(new Set())

  // 挂载时拉取一次已有任务列表（恢复历史任务）
  useEffect(() => {
    automationApi.listJobs()
      .then((list) => useAutomationStore.getState().syncBackendJobs(list))
      .catch(() => {
        // 后端尚未就绪时静默失败，后续动作会再次触发同步
      })
  }, [])

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
          source.close()
        }
      })
      source.addEventListener('error', () => {
        // 连接异常时关闭，下一轮活跃集合变化或轮询会重新同步
        source.close()
      })
      return source
    })

    return () => sources.forEach((source) => source.close())
  }, [activeKey])
}
