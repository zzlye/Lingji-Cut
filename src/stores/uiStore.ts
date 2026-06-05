// src/stores/uiStore.ts
// 界面导航状态 - 当前工作区、活动抽屉、设置分组，取代散落在 AppShell/Header 的本地 useState
import { create } from 'zustand'

/** 主工作区 */
export type Workspace = 'studio' | 'queue' | 'library' | 'subtitle' | 'history' | 'settings'

/** 设置分组 */
export type SettingsSection = 'effects' | 'export' | 'subtitle' | 'api' | 'voice' | 'glossary' | 'banned' | 'paths'

interface UiState {
  /** 当前工作区 */
  workspace: Workspace
  setWorkspace: (workspace: Workspace) => void
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
  isActivityOpen: false,
  setActivityOpen: (isActivityOpen) => set({ isActivityOpen }),
  toggleActivity: () => set((state) => ({ isActivityOpen: !state.isActivityOpen })),
  settingsSection: 'effects',
  setSettingsSection: (settingsSection) => set({ settingsSection }),
  openSettings: (section) => set((state) => ({ workspace: 'settings', settingsSection: section ?? state.settingsSection })),
}))
