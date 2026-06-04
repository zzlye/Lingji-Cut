// src/stores/logStore.ts
// 应用日志状态 - 从原 taskStore 拆出。warn/error 同步弹出 Toast，便于即时反馈
import { create } from 'zustand'
import { toast } from 'sonner'
import type { LogEntry } from '@/types'

interface LogState {
  /** 日志列表（仅保留最近若干条） */
  logs: LogEntry[]
  /** 追加一条日志；warn/error 会同步弹出 Toast */
  addLog: (level: LogEntry['level'], message: string) => void
  /** 清空日志 */
  clearLogs: () => void
}

export const useLogStore = create<LogState>((set) => ({
  logs: [],
  addLog: (level, message) => {
    // 错误和警告即时弹 Toast，info 仅记录到活动抽屉，避免刷屏
    if (level === 'error') toast.error(message)
    else if (level === 'warn') toast.warning(message)
    set((state) => ({
      logs: [...state.logs, { timestamp: new Date().toISOString(), level, message }].slice(-200),
    }))
  },
  clearLogs: () => set({ logs: [] }),
}))
