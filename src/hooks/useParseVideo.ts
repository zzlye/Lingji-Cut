// src/hooks/useParseVideo.ts
// 解析视频动作 - 封装解析 + 日志 + Toast，供工作台复用
import { useCallback } from 'react'
import { toast } from 'sonner'
import { videoApi } from '@/lib/api'
import { useVideoStore } from '@/stores/videoStore'
import { useLogStore } from '@/stores/logStore'
import type { VideoParseResult } from '@/types'

export function useParseVideo() {
  const isParsing = useVideoStore((s) => s.isParsing)
  const setParsing = useVideoStore((s) => s.setParsing)
  const setCurrentVideo = useVideoStore((s) => s.setCurrentVideo)
  const addLog = useLogStore((s) => s.addLog)

  /** 解析 YouTube 链接，成功返回视频信息，失败返回 null */
  const parse = useCallback(async (url: string): Promise<VideoParseResult | null> => {
    if (!url.trim() || useVideoStore.getState().isParsing) return null
    setParsing(true)
    addLog('info', `开始解析: ${url}`)
    try {
      const result = await videoApi.parse(url)
      setCurrentVideo(result)
      addLog('info', `解析成功: ${result.title ?? ''}`)
      toast.success(`解析成功：${result.title ?? url}`)
      return result
    } catch (error) {
      addLog('error', `解析失败: ${error instanceof Error ? error.message : '未知错误'}`)
      setCurrentVideo(null)
      return null
    } finally {
      setParsing(false)
    }
  }, [setParsing, setCurrentVideo, addLog])

  return { parse, isParsing }
}
