// src/components/layout/Header.tsx
// 顶部栏组件 - URL 输入、解析按钮、全局任务状态、窗口控制

import { useEffect, useRef, useState } from 'react'
import { automationApi, videoApi } from '@/lib/api'
import { buildAutomationPayload } from '@/lib/automationPayload'
import { useTaskStore } from '@/stores/taskStore'
import { SettingsCenter } from '@/features/settings/SettingsCenter'
import type { BackendAutomationJob } from '@/types'

/** 设置弹窗默认尺寸和窗口边距 */
const SETTINGS_MODAL_WIDTH = 960
const SETTINGS_MODAL_HEIGHT = 600
const SETTINGS_MODAL_MARGIN = 16

/** 设置弹窗坐标 */
type SettingsPosition = {
  x: number
  y: number
}

/** 设置弹窗拖动状态 */
type SettingsDragState = {
  startX: number
  startY: number
  originX: number
  originY: number
}

/** 限制弹窗位置，避免拖出窗口外 */
function clampSettingsPosition(x: number, y: number): SettingsPosition {
  if (typeof window === 'undefined') {
    return { x, y }
  }

  const modalWidth = Math.min(SETTINGS_MODAL_WIDTH, window.innerWidth - SETTINGS_MODAL_MARGIN * 2)
  const modalHeight = Math.min(SETTINGS_MODAL_HEIGHT, window.innerHeight - SETTINGS_MODAL_MARGIN * 2)
  const maxX = Math.max(SETTINGS_MODAL_MARGIN, window.innerWidth - modalWidth - SETTINGS_MODAL_MARGIN)
  const maxY = Math.max(SETTINGS_MODAL_MARGIN, window.innerHeight - modalHeight - SETTINGS_MODAL_MARGIN)

  return {
    x: Math.min(Math.max(SETTINGS_MODAL_MARGIN, x), maxX),
    y: Math.min(Math.max(SETTINGS_MODAL_MARGIN, y), maxY),
  }
}

/** 计算设置弹窗默认位置，靠右显示在顶部栏下方 */
function getDefaultSettingsPosition(): SettingsPosition {
  if (typeof window === 'undefined') {
    return { x: SETTINGS_MODAL_MARGIN, y: 64 }
  }

  const modalWidth = Math.min(SETTINGS_MODAL_WIDTH, window.innerWidth - SETTINGS_MODAL_MARGIN * 2)
  return clampSettingsPosition(window.innerWidth - modalWidth - SETTINGS_MODAL_MARGIN, 64)
}

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
  // 设置中心弹层是否打开
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  // 设置中心弹层位置
  const [settingsPosition, setSettingsPosition] = useState<SettingsPosition | null>(null)
  // 设置弹窗拖动过程中的起始数据
  const settingsDragRef = useRef<SettingsDragState | null>(null)
  // 全局状态
  const {
    setCurrentVideo,
    setParsing,
    addLog,
    addTask,
    upsertAutomationJob,
  } = useTaskStore()

  useEffect(() => {
    return () => {
      window.removeEventListener('mousemove', handleSettingsMouseMove)
      window.removeEventListener('mouseup', handleSettingsMouseUp)
    }
  }, [])

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
   * 启动后端后台编排器，并轮询同步解析、下载、画面处理、字幕、配音和导出进度。
   */
  const handleAutoRun = async () => {
    if (!url.trim() || isRunningAuto) return

    setIsRunningAuto(true)
    setParsing(true)
    addLog('info', '开始一键完成流程')

    try {
      const { job_id } = await automationApi.start(buildAutomationPayload(url))
      addLog('info', `自动处理任务已进入队列: ${job_id}`)

      const job = await watchAutomationJob(job_id)
      addLog('info', `一键完成流程已结束: ${job.output_path || '已导出'}`)
      try {
        const video = await videoApi.parse(url)
        setCurrentVideo(video)
      } catch {
        // 成品已经导出，刷新元数据失败只记录为弱提示。
        addLog('warn', '自动流程已完成，但刷新视频信息失败')
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '未知错误'
      addLog('error', `一键完成失败: ${message}`)
    } finally {
      setIsRunningAuto(false)
      setParsing(false)
    }
  }

  /** 监听后台自动化任务，优先使用 SSE，失败时降级为轮询 */
  const watchAutomationJob = (jobId: string) => new Promise<BackendAutomationJob>((resolve, reject) => {
    if (typeof EventSource === 'undefined') {
      pollAutomationJob(jobId).then(resolve).catch(reject)
      return
    }

    let settled = false
    const source = new EventSource(automationApi.eventsUrl(jobId))
    const fallbackTimer = window.setTimeout(() => {
      if (settled) return
      settled = true
      source.close()
      pollAutomationJob(jobId).then(resolve).catch(reject)
    }, 5000)

    source.addEventListener('job', (event) => {
      window.clearTimeout(fallbackTimer)
      const job = JSON.parse((event as MessageEvent).data) as BackendAutomationJob
      syncBackendAutomationJob(job)
      if (job.status === 'completed') {
        settled = true
        window.clearTimeout(fallbackTimer)
        source.close()
        resolve(job)
      }
      if (job.status === 'failed') {
        settled = true
        window.clearTimeout(fallbackTimer)
        source.close()
        reject(new Error(job.error_message || '自动化任务失败'))
      }
    })

    source.addEventListener('error', () => {
      if (settled) return
      settled = true
      window.clearTimeout(fallbackTimer)
      source.close()
      pollAutomationJob(jobId).then(resolve).catch(reject)
    })
  })

  /** SSE 不可用时按固定间隔查询后台任务 */
  const pollAutomationJob = async (jobId: string) => {
    for (;;) {
      const job = await automationApi.getJob(jobId)
      syncBackendAutomationJob(job)
      if (job.status === 'completed') return job
      if (job.status === 'failed') throw new Error(job.error_message || '自动化任务失败')
      await new Promise((resolve) => window.setTimeout(resolve, 1200))
    }
  }

  /** 把后端自动化任务映射到前端队列展示 */
  const syncBackendAutomationJob = (job: BackendAutomationJob) => {
    const steps = (['parse', 'download', 'effects', 'subtitle', 'voice', 'export'] as const).map((key) => {
      const stage = job.stages.find((item) => item.key === key)
      return {
        key,
        label: {
          parse: '解析视频',
          download: '下载入库',
          effects: '画面处理',
          subtitle: '字幕处理',
          voice: '配音生成',
          export: '合成导出',
        }[key],
        description: {
          parse: '读取 YouTube 元数据和字幕轨',
          download: '下载原视频并归档到项目目录',
          effects: '应用画面差异化和输出参数',
          subtitle: '生成、翻译、润色并渲染字幕',
          voice: '按配置生成或跳过配音',
          export: '合成视频、字幕、配音并导出成品',
        }[key],
        status: stage?.status === 'failed' ? 'failed' : stage?.status === 'skipped' ? 'skipped' : stage?.status === 'completed' ? 'completed' : job.status === 'running' && job.current_step?.includes({
          parse: '解析',
          download: '下载',
          effects: '画面',
          subtitle: '字幕',
          voice: '配音',
          export: '导出',
        }[key]) ? 'running' : 'pending',
        progress: stage?.status === 'completed' || stage?.status === 'skipped' ? 100 : Math.round(stage?.progress || 0),
        output_path: stage?.output_path || null,
        error_message: stage?.error_message || null,
      }
    })

    upsertAutomationJob({
      id: job.id,
      title: job.title || '一键自动流程',
      source_url: job.source_url,
      video_id: job.video_id,
      status: job.status,
      progress: Math.round(job.progress || 0),
      current_step: job.current_step || '等待开始',
      batch_id: job.batch_id,
      created_at: job.created_at || new Date().toISOString(),
      completed_at: job.completed_at,
      steps,
    })

    job.stages.forEach((stage) => {
      if (!stage.task_id || stage.key === 'parse' || stage.key === 'pipeline' || !job.video_id) return
      addTask({
        id: stage.task_id,
        video_id: job.video_id,
        task_type: stage.key,
        status: stage.status === 'failed' ? 'failed' : stage.status === 'completed' || stage.status === 'skipped' ? 'completed' : 'processing',
        progress: stage.status === 'completed' || stage.status === 'skipped' ? 100 : Math.round(stage.progress || 0),
        output_path: stage.output_path || null,
        error_message: stage.error_message || null,
        created_at: job.created_at || new Date().toISOString(),
        completed_at: stage.status === 'completed' || stage.status === 'skipped' ? new Date().toISOString() : null,
      })
    })
  }

  /**
   * 处理键盘回车事件
   */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleParse()
    }
  }

  /** 打开设置弹窗，并在第一次打开时放到右上角 */
  const handleOpenSettings = () => {
    setSettingsPosition((current) => current ? clampSettingsPosition(current.x, current.y) : getDefaultSettingsPosition())
    setIsSettingsOpen(true)
  }

  /** 开始拖动设置弹窗 */
  const handleSettingsDragStart = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return

    const target = event.target as HTMLElement
    if (target.closest('button,input,select,textarea,a')) return

    event.preventDefault()
    const position = settingsPosition || getDefaultSettingsPosition()
    settingsDragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
    }
    setSettingsPosition(position)
    window.addEventListener('mousemove', handleSettingsMouseMove)
    window.addEventListener('mouseup', handleSettingsMouseUp)
  }

  /** 拖动设置弹窗 */
  function handleSettingsMouseMove(event: MouseEvent) {
    const dragState = settingsDragRef.current
    if (!dragState) return

    event.preventDefault()
    setSettingsPosition(clampSettingsPosition(
      dragState.originX + event.clientX - dragState.startX,
      dragState.originY + event.clientY - dragState.startY,
    ))
  }

  /** 结束拖动设置弹窗 */
  function handleSettingsMouseUp() {
    settingsDragRef.current = null
    window.removeEventListener('mousemove', handleSettingsMouseMove)
    window.removeEventListener('mouseup', handleSettingsMouseUp)
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

      {/* 设置入口 - 放在原状态位置 */}
      <div className="flex items-center gap-2 shrink-0 no-drag">
        <button
          onClick={handleOpenSettings}
          className="w-10 h-9 flex items-center justify-center border border-border rounded-md text-foreground-muted hover:text-foreground hover:bg-white/5 transition-colors"
          title="设置"
          aria-label="设置"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </button>
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
        <div
          className="no-drag fixed z-50 w-[960px] max-w-[calc(100vw-32px)] rounded-lg border border-border-bright bg-background-elevated shadow-2xl"
          style={{
            left: `${(settingsPosition || getDefaultSettingsPosition()).x}px`,
            top: `${(settingsPosition || getDefaultSettingsPosition()).y}px`,
          }}
        >
          <SettingsCenter
            onClose={() => setIsSettingsOpen(false)}
            onDragStart={handleSettingsDragStart}
          />
        </div>
      )}
    </header>
  )
}
