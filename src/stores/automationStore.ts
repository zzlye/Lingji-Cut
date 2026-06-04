// src/stores/automationStore.ts
// 一键自动流程任务状态 - 从原 taskStore 拆出。SSE/轮询统一写入这里，组件只订阅
import { create } from 'zustand'
import type { AutomationJob, BackendAutomationJob } from '@/types'
import { mapBackendAutomationJob } from '@/lib/automationMapper'

interface AutomationState {
  /** 自动流程任务列表 */
  jobs: AutomationJob[]
  /** 写入或更新一个前端任务 */
  upsertJob: (job: AutomationJob) => void
  /** 用后端任务同步单个任务（自动映射） */
  syncBackendJob: (job: BackendAutomationJob) => void
  /** 用后端任务列表整体同步 */
  syncBackendJobs: (jobs: BackendAutomationJob[]) => void
  /** 局部更新某个任务 */
  updateJob: (id: string, updates: Partial<AutomationJob>) => void
  /** 移除某个任务 */
  removeJob: (id: string) => void
  /** 清理已结束（完成/失败/取消）的任务 */
  clearFinished: () => void
}

/** 写入或更新任务的纯函数 */
function upsert(jobs: AutomationJob[], job: AutomationJob): AutomationJob[] {
  return jobs.some((item) => item.id === job.id)
    ? jobs.map((item) => (item.id === job.id ? job : item))
    : [job, ...jobs]
}

export const useAutomationStore = create<AutomationState>((set) => ({
  jobs: [],
  upsertJob: (job) => set((state) => ({ jobs: upsert(state.jobs, job) })),
  syncBackendJob: (backendJob) => set((state) => ({ jobs: upsert(state.jobs, mapBackendAutomationJob(backendJob)) })),
  syncBackendJobs: (backendJobs) => set(() => ({ jobs: backendJobs.map(mapBackendAutomationJob) })),
  updateJob: (id, updates) => set((state) => ({ jobs: state.jobs.map((job) => (job.id === id ? { ...job, ...updates } : job)) })),
  removeJob: (id) => set((state) => ({ jobs: state.jobs.filter((job) => job.id !== id) })),
  clearFinished: () => set((state) => ({
    jobs: state.jobs.filter((job) => job.status === 'running' || job.status === 'pending' || job.status === 'paused'),
  })),
}))

/** 选择需要订阅实时进度的活跃任务（运行中或等待中） */
export function selectActiveJobIds(jobs: AutomationJob[]): string[] {
  return jobs.filter((job) => job.status === 'running' || job.status === 'pending').map((job) => job.id)
}
