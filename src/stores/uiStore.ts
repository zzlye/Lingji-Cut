// src/stores/uiStore.ts
// 界面导航状态 - 当前工作区、活动抽屉、设置分组，取代散落在 AppShell/Header 的本地 useState
import { create } from 'zustand'

/** 主工作区 */
export type Workspace = 'studio' | 'queue' | 'library' | 'subtitle' | 'history' | 'settings'

/** 设置分组 */
export type SettingsSection = 'effects' | 'export' | 'subtitle' | 'api' | 'prompts' | 'voice' | 'glossary' | 'banned' | 'paths'

interface UiState {
  /** 当前工作区 */
  workspace: Workspace
  setWorkspace: (workspace: Workspace) => void
  /** 字幕调整页当前选中的自动化任务 */
  subtitleJobId: string | null
  setSubtitleJobId: (jobId: string | null) => void
  /** 打开独立字幕调整页并可同时锁定任务 */
  openSubtitleWorkbench: (jobId?: string | null) => void
  /** 活动/日志抽屉是否打开 */
  isActivityOpen: boolean
  setActivityOpen: (open: boolean) => void
  toggleActivity: () => void
  /** 设置区当前分组 */
  settingsSection: SettingsSection
  setSettingsSection: (section: SettingsSection) => void
  /** 打开设置并定位到指定分组 */
  openSettings: (section?: SettingsSection) => void
}

export const useUiStore = create<UiState>((set) => ({
  workspace: 'studio',
  setWorkspace: (workspace) => set({ workspace }),
  subtitleJobId: null,
  setSubtitleJobId: (subtitleJobId) => set({ subtitleJobId }),
  openSubtitleWorkbench: (subtitleJobId) => set((state) => ({
    workspace: 'subtitle',
    subtitleJobId: subtitleJobId ?? state.subtitleJobId,
  })),
  isActivityOpen: false,
  setActivityOpen: (isActivityOpen) => set({ isActivityOpen }),
  toggleActivity: () => set((state) => ({ isActivityOpen: !state.isActivityOpen })),
  settingsSection: 'effects',
  setSettingsSection: (settingsSection) => set({ settingsSection }),
  openSettings: (section) => set((state) => ({ workspace: 'settings', settingsSection: section ?? state.settingsSection })),
}))
