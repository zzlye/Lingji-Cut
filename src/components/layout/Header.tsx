// src/components/layout/Header.tsx
// 顶部栏组件 - URL 输入、解析按钮、全局任务状态、窗口控制

import { useState } from 'react'
import { videoApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'

/**
 * 顶部栏组件
 * 包含：URL 输入框、解析按钮、全局任务状态、窗口控制按钮
 */
export function Header() {
  // 输入的 URL
  const [url, setUrl] = useState('')
  // 是否正在解析
  const [isParsing, setIsParsing] = useState(false)
  // 全局状态
  const { setCurrentVideo, setParsing, addLog } = useTaskStore()

  /**
   * 处理解析按钮点击
   * 调用后端 API 解析 YouTube URL
   */
  const handleParse = async () => {
    if (!url.trim() || isParsing) return

    setIsParsing(true)
    setParsing(true)
    addLog('info', `开始解析: ${url}`)

    try {
      const result = await videoApi.parse(url)
      setCurrentVideo(result)
      addLog('info', `解析成功: ${result.title}`)
    } catch (error) {
      addLog('error', `解析失败: ${error instanceof Error ? error.message : '未知错误'}`)
      setCurrentVideo(null)
    } finally {
      setIsParsing(false)
      setParsing(false)
    }
  }

  /**
   * 处理键盘回车事件
   */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleParse()
    }
  }

  return (
    <header className="titlebar-drag flex items-center h-14 px-4 bg-background-elevated border-b border-border gap-3">
      {/* 应用标题 */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="w-6 h-6 rounded bg-primary flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <span className="font-semibold text-sm">YouTube 视频处理器</span>
      </div>

      {/* URL 输入区域 */}
      <div className="flex-1 flex items-center gap-2 no-select">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="粘贴 YouTube 视频链接..."
          className="flex-1 h-9 px-3 bg-background border border-border rounded-md text-sm text-foreground placeholder:text-foreground-muted focus:outline-none focus:border-primary transition-colors"
        />
        <button
          onClick={handleParse}
          disabled={!url.trim() || isParsing}
          className="h-9 px-4 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isParsing ? '解析中...' : '解析'}
        </button>
      </div>

      {/* 全局任务状态 */}
      <div className="flex items-center gap-2 shrink-0 no-select">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-background/50 text-xs">
          <span className={`w-2 h-2 rounded-full ${isParsing ? 'bg-warning animate-pulse' : 'bg-success'}`} />
          <span className="text-foreground-muted">{isParsing ? '解析中' : '就绪'}</span>
        </div>
      </div>

      {/* 窗口控制按钮 */}
      <div className="flex items-center gap-1 shrink-0 no-select">
        <button
          onClick={() => window.electron?.window.minimize()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
          title="最小化"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
          </svg>
        </button>
        <button
          onClick={() => window.electron?.window.maximize()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
          title="最大化"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
          </svg>
        </button>
        <button
          onClick={() => window.electron?.window.close()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-destructive transition-colors"
          title="关闭"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </header>
  )
}
