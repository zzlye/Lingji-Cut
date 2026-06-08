// src/env.d.ts
// 环境变量与全局类型声明

/// <reference types="vite/client" />

/** 渲染进程可用的 Electron API（由 electron/preload/index.ts 通过 contextBridge 暴露） */
interface ElectronAPI {
  /** 窗口控制 */
  window: {
    minimize: () => void
    maximize: () => void
    close: () => void
  }
  /** 后端相关 */
  backend: {
    getUrl: () => Promise<string>
  }
  /** 系统路径 */
  shell: {
    openPath: (path: string) => Promise<string>
  }
  /** 系统对话框 */
  dialog: {
    selectDirectory: (defaultPath?: string) => Promise<string | null>
    selectVideoFile: () => Promise<string | null>
  }
}

interface Window {
  electron?: ElectronAPI
}
