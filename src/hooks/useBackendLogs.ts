// src/hooks/useBackendLogs.ts
// 后端活动日志同步 - 把 CMD 控制台里的业务日志同步到前端活动日志抽屉
import { useEffect, useRef } from 'react'
import { logsApi } from '@/lib/api'
import { useLogStore } from '@/stores/logStore'
import type { BackendLogEntry } from '@/types'

const BACKEND_LOG_POLL_INTERVAL_MS = 2000
const MAX_SEEN_LOG_KEYS = 500

function backendLogKey(log: BackendLogEntry) {
  return `${log.id}|${log.timestamp}|${log.level}|${log.source}|${log.message}`
}

export function useBackendLogs() {
  const seenRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof window.setInterval> | null = null

    const trimSeenKeys = () => {
      // 去重缓存只保留近期键，避免长时间运行后无限增长
      while (seenRef.current.size > MAX_SEEN_LOG_KEYS) {
        const first = seenRef.current.values().next().value
        if (!first) break
        seenRef.current.delete(first)
      }
    }

    const syncLogs = async () => {
      try {
        const logs = await logsApi.list()
        if (cancelled) return
        const addLog = useLogStore.getState().addLog
        for (const log of logs) {
          const key = backendLogKey(log)
          if (seenRef.current.has(key)) continue
          seenRef.current.add(key)
          addLog(log.level, `[${log.source}] ${log.message}`, {
            timestamp: log.timestamp,
            toast: false,
            source: log.source,
          })
        }
        trimSeenKeys()
      } catch {
        // 后端启动中或重启中时静默，下一轮轮询会自动恢复
      }
    }

    void syncLogs()
    timer = window.setInterval(() => {
      void syncLogs()
    }, BACKEND_LOG_POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
    }
  }, [])
}
