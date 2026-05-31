// src/stores/taskStore.ts
// 任务状态管理 - 使用 Zustand 管理全局任务状态

import { create } from 'zustand'
import type { DownloadTask, LogEntry, VideoParseResult } from '@/types'

/** 任务状态接口 */
interface TaskState {
  /** 任务列表 */
  tasks: DownloadTask[]
  /** 当前解析的视频信息 */
  currentVideo: VideoParseResult | null
  /** 日志列表 */
  logs: LogEntry[]
  /** 是否正在解析 */
  isParsing: boolean

  /** 添加任务 */
  addTask: (task: DownloadTask) => void
  /** 更新任务状态 */
  updateTask: (id: number, updates: Partial<DownloadTask>) => void
  /** 设置当前视频 */
  setCurrentVideo: (video: VideoParseResult | null) => void
  /** 添加日志 */
  addLog: (level: LogEntry['level'], message: string) => void
  /** 设置解析状态 */
  setParsing: (isParsing: boolean) => void
}

/** 创建任务状态 Store */
export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  currentVideo: null,
  logs: [],
  isParsing: false,

  addTask: (task) =>
    set((state) => ({ tasks: [task, ...state.tasks] })),

  updateTask: (id, updates) =>
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, ...updates } : t
      ),
    })),

  setCurrentVideo: (video) =>
    set({ currentVideo: video }),

  addLog: (level, message) =>
    set((state) => ({
      logs: [
        ...state.logs,
        {
          timestamp: new Date().toISOString(),
          level,
          message,
        },
      ].slice(-100), // 只保留最近 100 条日志
    })),

  setParsing: (isParsing) =>
    set({ isParsing }),
}))
