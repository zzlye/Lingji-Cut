// src/hooks/useAutoRun.ts
// 一键完成动作 - 启动后台自动流程并把任务交给全局 SSE 流接管进度
import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { automationApi, videoApi } from '@/lib/api'
import { buildAutomationPayload, isLocalVideoSource } from '@/lib/automationPayload'
import { useAutomationStore } from '@/stores/automationStore'
import { useLogStore } from '@/stores/logStore'
import { useVideoStore } from '@/stores/videoStore'

export function useAutoRun() {
  const [isStarting, setIsStarting] = useState(false)
  const syncBackendJob = useAutomationStore((s) => s.syncBackendJob)
  const addLog = useLogStore((s) => s.addLog)
  const setCurrentVideo = useVideoStore((s) => s.setCurrentVideo)
  const setParsing = useVideoStore((s) => s.setParsing)

  /** 启动一键完成流程；进度由 useAutomationStream 实时推进 */
  const start = useCallback(async (source: string): Promise<boolean> => {
    const trimmedSource = source.trim()
    if (!trimmedSource || isStarting) return false
    const isLocalSource = isLocalVideoSource(trimmedSource)
    setIsStarting(true)
    addLog('info', '提交一键完成流程')
    try {
      // 链接来源先做前端解析；本地视频直接交给后端建库并继续流程。
      if (isLocalSource) {
        addLog('info', '已选择本地视频，跳过链接解析并直接提交一键流程')
      } else {
        setCurrentVideo(null)
        setParsing(true)
        try {
          const parsedVideo = await videoApi.parse(trimmedSource)
          setCurrentVideo(parsedVideo)
          addLog('info', `启动前解析完成: ${parsedVideo.title ?? trimmedSource}`)
        } catch (error) {
          addLog('warn', `启动前解析失败，继续提交后台任务: ${error instanceof Error ? error.message : '未知错误'}`)
        } finally {
          setParsing(false)
        }
      }

      const { job_id } = await automationApi.start(buildAutomationPayload(trimmedSource))
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
      setParsing(false)
      setIsStarting(false)
    }
  }, [isStarting, syncBackendJob, addLog, setCurrentVideo, setParsing])

  return { start, isStarting }
}
