// src/features/tasks/TaskPanel.tsx
// 任务面板组件 - 显示任务列表和当前视频信息

import { useTaskStore } from '@/stores/taskStore'
import { VideoInfoCard } from './VideoInfoCard'

/**
 * 任务面板
 * 显示当前解析的视频信息和任务列表
 */
export function TaskPanel() {
  const { currentVideo, tasks } = useTaskStore()

  return (
    <div className="h-full flex flex-col gap-4">
      {/* 视频信息卡片 - 解析成功后显示 */}
      {currentVideo && (
        <VideoInfoCard video={currentVideo} />
      )}

      {/* 任务列表 */}
      <div className="flex-1">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">任务队列</h3>

        {tasks.length === 0 ? (
          <EmptyTaskList />
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => (
              <TaskItem key={task.id} task={task} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** 空任务列表 */
function EmptyTaskList() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-foreground-muted">
      <svg className="w-12 h-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
      <p className="text-sm">暂无任务</p>
      <p className="text-xs mt-1">在顶部输入 YouTube URL 开始下载</p>
    </div>
  )
}

/** 任务项 */
function TaskItem({ task }: { task: import('@/types').DownloadTask }) {
  // 状态颜色映射
  const statusColors: Record<string, string> = {
    pending: 'bg-foreground-muted',
    downloading: 'bg-accent',
    processing: 'bg-warning',
    completed: 'bg-success',
    failed: 'bg-destructive',
  }

  // 状态文本映射
  const statusTexts: Record<string, string> = {
    pending: '等待中',
    downloading: '下载中',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
  }

  return (
    <div className="flex items-center gap-3 p-3 bg-background-elevated rounded-lg border border-border">
      {/* 状态指示点 */}
      <span className={`w-2 h-2 rounded-full ${statusColors[task.status]}`} />

      {/* 任务信息 */}
      <div className="flex-1 min-w-0">
        <p className="text-sm truncate">{taskTypeText(task.task_type)}</p>
        <p className="text-xs text-foreground-muted">{statusTexts[task.status]}</p>
      </div>

      {/* 进度 */}
      {task.status === 'downloading' && (
        <div className="w-20">
          <div className="h-1.5 bg-background rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-300"
              style={{ width: `${task.progress}%` }}
            />
          </div>
          <p className="text-xs text-foreground-muted text-right mt-0.5">{Math.round(task.progress)}%</p>
        </div>
      )}
    </div>
  )
}

/** 任务类型显示文本 */
function taskTypeText(type: import('@/types').DownloadTask['task_type']) {
  const texts: Record<import('@/types').DownloadTask['task_type'], string> = {
    download: '视频下载',
    effects: '画面处理',
    subtitle: '字幕处理',
    voice: '配音生成',
    export: '视频导出',
  }
  return texts[type] || type
}
