// electron/shared/types.ts
// 主进程和渲染进程共享的类型定义

/** Electron API 类型声明 */
export interface ElectronAPI {
  window: {
    minimize: () => void
    maximize: () => void
    close: () => void
  }
  backend: {
    getUrl: () => Promise<string>
  }
  shell: {
    openPath: (path: string) => Promise<string>
  }
  dialog: {
    selectDirectory: (defaultPath?: string) => Promise<string | null>
    selectVideoFile: () => Promise<string | null>
  }
}

/** 全局 window 对象扩展 */
declare global {
  interface Window {
    electron?: ElectronAPI
  }
}
