// electron/main/index.ts
// Electron 主进程入口 - 创建窗口、管理应用生命周期、启动 Python 后端
import { app, BrowserWindow, shell, ipcMain, dialog } from 'electron'
import { join } from 'path'
import { existsSync } from 'fs'
import { is } from '@electron-toolkit/utils'
import { spawn, ChildProcess } from 'child_process'
import { net } from 'electron'

// Python 后端进程引用
let pythonProcess: ChildProcess | null = null
// 后端服务地址
const BACKEND_URL = 'http://127.0.0.1:8765'
// 优先使用 D:\tools 下的 Python，避免 Windows Store python.exe 占位程序导致后端启动失败
const TOOLS_PYTHON = 'D:\\tools\\python-3.12.10-embed\\python.exe'

/**
 * 创建主窗口
 * 配置窗口大小、图标、Web 首选项等
 */
function createWindow(): BrowserWindow {
  const mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    show: false,
    // 无边框窗口，自定义标题栏
    frame: false,
    // 窗口背景色（与暗色主题一致）
    backgroundColor: '#0F172A',
    webPreferences: {
      // 预加载脚本路径
      preload: join(__dirname, '../preload/index.js'),
      // 安全设置：启用上下文隔离，禁用 Node.js 集成
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  // 窗口准备好后显示，避免白屏闪烁
  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  // 处理外部链接，在默认浏览器中打开
  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // 开发模式加载开发服务器，生产模式加载本地文件
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return mainWindow
}

/**
 * 启动 Python FastAPI 后端
 * 使用 child_process.spawn 启动后端进程
 */
function startPythonBackend(): void {
  // 项目根目录用于启动 Python 包，避免相对导入失效
  const projectRoot = join(__dirname, '../..')
  const backendRunner = join(projectRoot, 'backend/run.py')
  const pythonCommand = existsSync(TOOLS_PYTHON) ? TOOLS_PYTHON : 'py'
  const pythonArgs = existsSync(TOOLS_PYTHON) ? [backendRunner] : ['-3', backendRunner]

  // 使用 Python 启动 FastAPI
  pythonProcess = spawn(pythonCommand, pythonArgs, {
    // 标准输出重定向到控制台
    stdio: ['pipe', 'pipe', 'pipe'],
    // Windows 下使用 shell 模式
    shell: true,
    // 从项目根目录启动，确保 backend 包可被 Python 找到
    cwd: projectRoot,
    // 环境变量
    env: {
      ...process.env,
      PYTHONPATH: projectRoot,
      PYTHONIOENCODING: 'utf-8'
    }
  })

  // 监听后端标准输出
  pythonProcess.stdout?.on('data', (data) => {
    console.log(`[Python] ${data.toString().trim()}`)
  })

  // 监听后端错误输出
  pythonProcess.stderr?.on('data', (data) => {
    console.error(`[Python Error] ${data.toString().trim()}`)
  })

  // 监听后端进程退出
  pythonProcess.on('close', (code) => {
    console.log(`[Python] 后端进程退出，退出码: ${code}`)
    pythonProcess = null
  })
}

/**
 * 检查后端健康状态
 * 轮询 /health 端点直到后端就绪
 */
function checkBackendHealth(): Promise<boolean> {
  return new Promise((resolve) => {
    let attempts = 0
    const maxAttempts = 30

    const check = () => {
      attempts++
      const request = net.request(`${BACKEND_URL}/health`)

      request.on('response', () => {
        resolve(true)
      })

      request.on('error', () => {
        if (attempts >= maxAttempts) {
          resolve(false)
        } else {
          setTimeout(check, 1000)
        }
      })

      request.end()
    }

    check()
  })
}

// 应用准备就绪时创建窗口
app.whenReady().then(async () => {
  // 设置 IPC 处理器 - 窗口控制
  ipcMain.on('window:minimize', (event) => {
    BrowserWindow.fromWebContents(event.sender)?.minimize()
  })

  ipcMain.on('window:maximize', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender)
    if (win?.isMaximized()) {
      win.unmaximize()
    } else {
      win?.maximize()
    }
  })

  ipcMain.on('window:close', (event) => {
    BrowserWindow.fromWebContents(event.sender)?.close()
  })

  // 获取后端地址
  ipcMain.handle('get-backend-url', () => BACKEND_URL)

  // 选择项目目录，供文件位置设置使用
  ipcMain.handle('dialog:select-directory', async (_event, defaultPath?: string) => {
    const result = await dialog.showOpenDialog({
      title: '选择项目目录',
      defaultPath,
      properties: ['openDirectory', 'createDirectory'],
    })

    if (result.canceled || result.filePaths.length === 0) {
      return null
    }
    return result.filePaths[0]
  })

  // 启动 Python 后端
  startPythonBackend()

  // 等待后端就绪
  const isHealthy = await checkBackendHealth()
  if (!isHealthy) {
    console.error('[Main] Python 后端启动超时')
    // 后端未就绪时明确告知用户，避免静默进入一个所有请求都失败的空界面
    dialog.showErrorBox(
      '后端服务启动失败',
      [
        '本地处理服务（端口 8765）未能在预期时间内就绪。',
        '',
        '可能原因：',
        '• Python 运行环境或依赖缺失',
        '• 端口 8765 被其他程序占用',
        '• 安全软件拦截了本地服务',
        '',
        '界面仍会打开，但解析、下载、处理等功能将无法使用。',
        '请关闭应用后重试，或排查以上问题。',
      ].join('\n')
    )
  }

  // 创建窗口
  createWindow()

  // macOS: 点击 dock 图标时重新创建窗口
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

// 所有窗口关闭时退出应用（macOS 除外）
app.on('window-all-closed', () => {
  // 关闭 Python 后端
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }

  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// 应用退出前清理
app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
})
