// src/stores/prefsStore.ts
// 一键自动化偏好状态 - 包装现有 localStorage 偏好读写，让多个组件改动后自动同步
// （取代各组件各自 loadAutomationPreferences + setPreferenceVersion 强刷的旧做法）
import { create } from 'zustand'
import type { AutomationPreferences } from '@/types'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'

interface PrefsState {
  /** 当前一键自动化偏好 */
  preferences: AutomationPreferences
  /** 更新偏好（写入 localStorage 并同步到所有订阅组件） */
  update: (updates: Partial<AutomationPreferences>) => void
  /** 从 localStorage 重新载入 */
  reload: () => void
}

export const usePrefsStore = create<PrefsState>((set) => ({
  preferences: loadAutomationPreferences(),
  update: (updates) => set({ preferences: saveAutomationPreferences(updates) }),
  reload: () => set({ preferences: loadAutomationPreferences() }),
}))
