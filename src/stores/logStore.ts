// src/stores/logStore.ts
// 应用日志状态 - 从原 taskStore 拆出。warn/error 同步弹出 Toast，便于即时反馈
import { create } from 'zustand'
import { toast } from 'sonner'
import type { LogEntry } from '@/types'

const MAX_ACTIVITY_LOGS = 200

type AddLogOptions = {
  /** 后端同步历史日志时复用原始时间 */
  timestamp?: string
  /** 历史日志默认不弹 Toast，避免重启后刷屏 */
  toast?: boolean
  /** 日志来源，用于后续排查来源 */
  source?: string
}

interface LogState {
  /** 日志列表（仅保留最近若干条） */
  logs: LogEntry[]
  /** 追加一条日志；warn/error 会同步弹出 Toast */
  addLog: (level: LogEntry['level'], message: string, options?: AddLogOptions) => void
  /** 清空日志 */
  clearLogs: () => void
}

export const useLogStore = create<LogState>((set) => ({
  logs: [],
  addLog: (level, message, options) => {
    // 错误和警告即时弹 Toast，info 仅记录到活动抽屉，避免刷屏
    const shouldToast = options?.toast ?? true
    if (shouldToast && level === 'error') toast.error(message)
    else if (shouldToast && level === 'warn') toast.warning(message)
    set((state) => ({
      logs: [...state.logs, { timestamp: options?.timestamp || new Date().toISOString(), level, message, source: options?.source }].slice(-MAX_ACTIVITY_LOGS),
    }))
  },
  clearLogs: () => set({ logs: [] }),
}))
