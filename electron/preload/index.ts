// electron/preload/index.ts
// Electron 预加载脚本 - 通过 contextBridge 安全地暴露 IPC API 到渲染进程
import { contextBridge, ipcRenderer } from 'electron'

/**
 * 暴露到渲染进程的 API
 * 所有与主进程的通信都通过此 API 进行
 */
const electronAPI = {
  // 窗口控制
  window: {
    /** 最小化窗口 */
    minimize: () => ipcRenderer.send('window:minimize'),
    /** 最大化/还原窗口 */
    maximize: () => ipcRenderer.send('window:maximize'),
    /** 关闭窗口 */
    close: () => ipcRenderer.send('window:close')
  },
  // 后端相关
  backend: {
    /** 获取后端服务地址 */
    getUrl: () => ipcRenderer.invoke('get-backend-url')
  },
  // 系统路径
  shell: {
    /** 打开本地文件或文件夹 */
    openPath: (path: string) => ipcRenderer.invoke('shell:open-path', path)
  },
  // 系统对话框
  dialog: {
    /** 选择项目目录 */
    selectDirectory: (defaultPath?: string) => ipcRenderer.invoke('dialog:select-directory', defaultPath)
  }
}

// 将 API 暴露到渲染进程的 window.electron 对象
contextBridge.exposeInMainWorld('electron', electronAPI)
