// src/components/layout/LogPanel.tsx
// 底部日志面板组件 - 展示应用日志、错误信息、任务状态

import { useTaskStore } from '@/stores/taskStore'

/** 日志面板属性 */
interface LogPanelProps {
  /** 是否展开 */
  isExpanded: boolean
  /** 切换展开/收起 */
  onToggle: () => void
}

/**
 * 底部日志面板
 * 支持展开/收起，展示应用运行日志
 */
export function LogPanel({ isExpanded, onToggle }: LogPanelProps) {
  const { logs } = useTaskStore()

  /** 格式化时间戳 */
  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp)
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  /** 日志级别颜色 */
  const levelColors: Record<string, string> = {
    info: 'text-accent',
    warn: 'text-warning',
    error: 'text-destructive',
  }

  return (
    <div
      className={`
        bg-background-elevated border-t border-border transition-all duration-200
        ${isExpanded ? 'h-48' : 'h-8'}
      `}
    >
      {/* 日志面板头部 - 点击展开/收起 */}
      <button
        onClick={onToggle}
        className="w-full h-8 px-4 flex items-center justify-between text-xs text-foreground-muted hover:text-foreground transition-colors"
      >
        <div className="flex items-center gap-2">
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span>日志</span>
          {logs.length > 0 && (
            <span className="px-1.5 py-0.5 bg-background rounded text-[10px]">
              {logs.length}
            </span>
          )}
        </div>
        <svg
          className={`w-3.5 h-3.5 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
        </svg>
      </button>

      {/* 日志内容区域 */}
      {isExpanded && (
        <div className="h-[calc(100%-2rem)] overflow-auto px-4 pb-2 font-mono">
          {logs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-foreground-muted text-xs">
              暂无日志输出
            </div>
          ) : (
            <div className="space-y-0.5">
              {logs.map((log, index) => (
                <div key={index} className="flex items-start gap-2 text-xs">
                  <span className="text-foreground-muted shrink-0">
                    {formatTime(log.timestamp)}
                  </span>
                  <span className={`shrink-0 uppercase ${levelColors[log.level]}`}>
                    [{log.level}]
                  </span>
                  <span className="break-all">{log.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
