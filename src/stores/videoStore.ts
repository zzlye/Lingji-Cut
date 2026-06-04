// src/stores/videoStore.ts
// 当前解析视频状态 - 从原 taskStore 拆出，职责单一
import { create } from 'zustand'
import type { VideoParseResult } from '@/types'

interface VideoState {
  /** 当前解析的视频信息 */
  currentVideo: VideoParseResult | null
  /** 是否正在解析 */
  isParsing: boolean
  /** 设置当前视频 */
  setCurrentVideo: (video: VideoParseResult | null) => void
  /** 设置解析状态 */
  setParsing: (isParsing: boolean) => void
}

export const useVideoStore = create<VideoState>((set) => ({
  currentVideo: null,
  isParsing: false,
  setCurrentVideo: (video) => set({ currentVideo: video }),
  setParsing: (isParsing) => set({ isParsing }),
}))
