// src/stores/taskStore.ts
// 任务状态管理 - 使用 Zustand 管理全局任务状态

import { create } from 'zustand'
import type { AutomationJob, AutomationStep, DownloadTask, LogEntry, VideoParseResult } from '@/types'

/** 自动处理默认步骤 */
const AUTOMATION_STEPS: AutomationStep[] = [
  { key: 'parse', label: '解析视频', description: '读取 YouTube 元数据和字幕轨', status: 'pending', progress: 0 },
  { key: 'download', label: '下载入库', description: '下载原视频并归档到项目目录', status: 'pending', progress: 0 },
  { key: 'effects', label: '画面处理', description: '应用画面差异化和输出参数', status: 'pending', progress: 0 },
  { key: 'subtitle', label: '字幕处理', description: '生成、翻译、润色并渲染字幕', status: 'pending', progress: 0 },
  { key: 'voice', label: '配音生成', description: '按配置生成或跳过配音', status: 'pending', progress: 0 },
  { key: 'export', label: '合成导出', description: '合成视频、字幕、配音并导出成品', status: 'pending', progress: 0 },
]

/** 生成自动处理任务 ID */
function createAutomationId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `auto-${Date.now()}-${Math.round(Math.random() * 10000)}`
}

/** 计算自动处理整体进度 */
function calculateAutomationProgress(steps: AutomationStep[]) {
  if (steps.length === 0) return 0
  const total = steps.reduce((sum, step) => sum + step.progress, 0)
  return Math.round(total / steps.length)
}

/** 任务状态接口 */
interface TaskState {
  /** 任务列表 */
  tasks: DownloadTask[]
  /** 一键自动处理流程列表 */
  automationJobs: AutomationJob[]
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
  /** 删除本地任务缓存 */
  removeTask: (id: number) => void
  /** 按条件清理本地任务缓存 */
  clearTasks: (status?: DownloadTask['status']) => void
  /** 创建一键自动处理流程 */
  startAutomationJob: (sourceUrl: string) => string
  /** 写入后端返回的一键自动处理流程 */
  upsertAutomationJob: (job: AutomationJob) => void
  /** 更新一键自动处理流程 */
  updateAutomationJob: (id: string, updates: Partial<AutomationJob>) => void
  /** 更新一键自动处理流程步骤 */
  updateAutomationStep: (id: string, stepKey: AutomationStep['key'], updates: Partial<AutomationStep>) => void
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
  automationJobs: [],
  currentVideo: null,
  logs: [],
  isParsing: false,

  addTask: (task) =>
    set((state) => {
      const exists = state.tasks.some((item) => item.id === task.id)
      return {
        tasks: exists
          ? state.tasks.map((item) => item.id === task.id ? { ...item, ...task } : item)
          : [task, ...state.tasks],
      }
    }),

  updateTask: (id, updates) =>
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, ...updates } : t
      ),
    })),

  removeTask: (id) =>
    set((state) => ({
      tasks: state.tasks.filter((task) => task.id !== id),
    })),

  clearTasks: (status) =>
    set((state) => ({
      tasks: status
        ? state.tasks.filter((task) => task.status !== status)
        : state.tasks.filter((task) => task.status === 'processing' || task.status === 'downloading'),
    })),

  startAutomationJob: (sourceUrl) => {
    const id = createAutomationId()
    const job: AutomationJob = {
      id,
      title: '一键自动流程',
      source_url: sourceUrl,
      video_id: null,
      status: 'pending',
      progress: 0,
      current_step: '等待开始',
      batch_id: null,
      created_at: new Date().toISOString(),
      completed_at: null,
      steps: AUTOMATION_STEPS.map((step) => ({ ...step })),
    }
    set((state) => ({ automationJobs: [job, ...state.automationJobs] }))
    return id
  },

  upsertAutomationJob: (job) =>
    set((state) => {
      const exists = state.automationJobs.some((item) => item.id === job.id)
      return {
        automationJobs: exists
          ? state.automationJobs.map((item) => item.id === job.id ? job : item)
          : [job, ...state.automationJobs],
      }
    }),

  updateAutomationJob: (id, updates) =>
    set((state) => ({
      automationJobs: state.automationJobs.map((job) =>
        job.id === id ? { ...job, ...updates } : job
      ),
    })),

  updateAutomationStep: (id, stepKey, updates) =>
    set((state) => ({
      automationJobs: state.automationJobs.map((job) => {
        if (job.id !== id) return job
        const steps = job.steps.map((step) =>
          step.key === stepKey ? { ...step, ...updates } : step
        )
        return {
          ...job,
          steps,
          progress: calculateAutomationProgress(steps),
          current_step: steps.find((step) => step.status === 'running')?.label || job.current_step,
        }
      }),
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
