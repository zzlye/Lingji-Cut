// src/stores/subtitleDraftStore.ts
// 字幕调整草稿缓存 - 切换素材库/历史/设置后保留当前校对位置和未保存输入
import { create } from 'zustand'
import type { SubtitleEntry } from '@/types'

export type SubtitleDraft = {
  subtitlePath: string
  outputPath: string
  fileName: string
  entries: SubtitleEntry[]
  selectedIndex: number
  checkedEntryIndexes: number[]
  entryKeyword: string
  listScrollTop: number
  updatedAt: number
}

interface SubtitleDraftState {
  drafts: Record<string, SubtitleDraft>
  getDraft: (key: string) => SubtitleDraft | null
  saveDraft: (key: string, draft: SubtitleDraft) => void
  clearDraft: (key: string) => void
}

export const useSubtitleDraftStore = create<SubtitleDraftState>((set, get) => ({
  drafts: {},
  getDraft: (key) => get().drafts[key] || null,
  saveDraft: (key, draft) => set((state) => ({
    drafts: {
      ...state.drafts,
      [key]: { ...draft, updatedAt: Date.now() },
    },
  })),
  clearDraft: (key) => set((state) => {
    const { [key]: _removedDraft, ...drafts } = state.drafts
    return { drafts }
  }),
}))

