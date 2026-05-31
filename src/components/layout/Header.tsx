// src/components/layout/Header.tsx
// 顶部栏组件 - URL 输入、解析按钮、全局任务状态、窗口控制

import { useState } from 'react'
import { videoApi, effectsApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import { EffectsSettingsPanel, loadAutomationConfig } from '@/features/effects/EffectsPanel'

/**
 * 顶部栏组件
 * 包含：URL 输入框、解析按钮、全局任务状态、窗口控制按钮
 */
export function Header() {
  // 输入的 URL
  const [url, setUrl] = useState('')
  // 是否正在解析
  const [isParsing, setIsParsing] = useState(false)
  // 是否正在执行一键流程
  const [isRunningAuto, setIsRunningAuto] = useState(false)
  // 自动化设置弹层是否打开
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  // 全局状态
  const { setCurrentVideo, setParsing, addLog, addTask } = useTaskStore()

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
   * 一键完成流程
   * 当前先自动执行解析、下载、画面处理；字幕和配音会在对应配置接入后继续串联。
   */
  const handleAutoRun = async () => {
    if (!url.trim() || isRunningAuto) return

    setIsRunningAuto(true)
    setParsing(true)
    addLog('info', '开始一键完成流程')

    try {
      const video = await videoApi.parse(url)
      setCurrentVideo(video)
      addLog('info', `解析成功: ${video.title}`)

      const download = await videoApi.download(video.id)
      addTask({
        id: download.task_id,
        video_id: video.id,
        task_type: 'download',
        status: download.output_path ? 'completed' : 'pending',
        progress: download.output_path ? 100 : 0,
        output_path: download.output_path || null,
        error_message: null,
        created_at: new Date().toISOString(),
        completed_at: download.output_path ? new Date().toISOString() : null,
      })

      if (!download.output_path) {
        addLog('warn', '下载任务已创建，但未返回输出路径')
        return
      }

      const effects = await effectsApi.apply({
        video_path: download.output_path,
        preset: loadAutomationConfig(),
      })

      if (effects.task_id) {
        addTask({
          id: effects.task_id,
          video_id: video.id,
          task_type: 'effects',
          status: 'completed',
          progress: 100,
          output_path: effects.output_path,
          error_message: null,
          created_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
        })
      }

      addLog('info', `一键完成流程已结束: ${effects.output_path}`)
    } catch (error) {
      addLog('error', `一键完成失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsRunningAuto(false)
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
    <header className="titlebar-drag relative flex items-center h-14 px-4 bg-background-elevated border-b border-border gap-3">
      {/* URL 输入区域 */}
      <div className="flex-1 flex items-center gap-2 no-drag">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="粘贴 YouTube 视频链接..."
          className="flex-1 h-9 px-3 bg-background border border-border rounded-md text-sm text-foreground placeholder:text-foreground-muted focus:outline-none focus:border-primary transition-colors"
        />
        <button
          onClick={() => setIsSettingsOpen(true)}
          className="w-10 h-9 flex items-center justify-center border border-border rounded-md text-foreground-muted hover:text-foreground hover:bg-white/5 transition-colors"
          title="自动化参数设置"
          aria-label="自动化参数设置"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </button>
        <button
          onClick={handleParse}
          disabled={!url.trim() || isParsing}
          className="h-9 min-w-28 px-6 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isParsing ? '解析中...' : '解析'}
        </button>
        <button
          onClick={handleAutoRun}
          disabled={!url.trim() || isRunningAuto}
          className="h-9 min-w-32 px-5 bg-accent text-accent-foreground rounded-md text-sm font-medium hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isRunningAuto ? '执行中...' : '一键完成'}
        </button>
      </div>

      {/* 全局任务状态 */}
      <div className="flex items-center gap-2 shrink-0 no-drag">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-background/50 text-xs">
          <span className={`w-2 h-2 rounded-full ${isParsing ? 'bg-warning animate-pulse' : 'bg-success'}`} />
          <span className="text-foreground-muted">{isRunningAuto ? '自动化' : isParsing ? '解析中' : '就绪'}</span>
        </div>
      </div>

      {/* 窗口控制按钮 */}
      <div className="flex items-center gap-1 shrink-0 no-drag">
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

      {isSettingsOpen && (
        <div className="no-drag absolute left-4 right-4 top-[calc(100%+8px)] z-50 rounded-lg border border-border-bright bg-background-elevated shadow-2xl">
          <EffectsSettingsPanel variant="compact" onClose={() => setIsSettingsOpen(false)} />
        </div>
      )}
    </header>
  )
}
