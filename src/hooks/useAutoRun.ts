// src/hooks/useAutoRun.ts
// 一键完成动作 - 启动后台自动流程并把任务交给全局 SSE 流接管进度
import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { automationApi } from '@/lib/api'
import { buildAutomationPayload } from '@/lib/automationPayload'
import { useAutomationStore } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'

export function useAutoRun() {
  const [isStarting, setIsStarting] = useState(false)
  const syncBackendJob = useAutomationStore((s) => s.syncBackendJob)
  const addLog = useLogStore((s) => s.addLog)

  /** 启动一键完成流程；进度由 useAutomationStream 实时推进 */
  const start = useCallback(async (url: string): Promise<boolean> => {
    if (!url.trim() || isStarting) return false
    setIsStarting(true)
    addLog('info', '提交一键完成流程')
    try {
      const { job_id } = await automationApi.start(buildAutomationPayload(url))
      addLog('info', `自动处理任务已进入队列: ${job_id}`)
      toast.success('已加入处理队列，进度将实时显示')
      // 立即拉一次写入 store，随后由全局 SSE 流持续更新
      try {
        const job = await automationApi.getJob(job_id)
        syncBackendJob(job)
      } catch {
        // 首次查询失败不影响后续 SSE 推送
      }
      return true
    } catch (error) {
      addLog('error', `一键完成失败: ${error instanceof Error ? error.message : '未知错误'}`)
      return false
    } finally {
      setIsStarting(false)
    }
  }, [isStarting, syncBackendJob, addLog])

  return { start, isStarting }
}
