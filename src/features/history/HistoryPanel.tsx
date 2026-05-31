// src/features/history/HistoryPanel.tsx
// 历史记录面板 - 显示历史下载和处理记录

import { useTaskStore } from '@/stores/taskStore'

/**
 * 历史记录面板
 * 显示已完成的任务记录
 */
export function HistoryPanel() {
  const { tasks } = useTaskStore()

  // 筛选已完成或失败的任务
  const historyTasks = tasks.filter(
    (t) => t.status === 'completed' || t.status === 'failed'
  )

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-medium">历史记录</h3>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {historyTasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-foreground-muted">
            <svg className="w-12 h-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p className="text-sm">暂无历史记录</p>
            <p className="text-xs mt-1">完成的任务将显示在这里</p>
          </div>
        ) : (
          <div className="space-y-2">
            {historyTasks.map((task) => (
              <div
                key={task.id}
                className="p-3 bg-background-elevated rounded-lg border border-border"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      task.status === 'completed' ? 'bg-success' : 'bg-destructive'
                    }`}
                  />
                  <span className="text-sm font-medium">
                    {task.task_type === 'download' ? '视频下载' : task.task_type}
                  </span>
                </div>
                {task.output_path && (
                  <p className="text-xs text-foreground-muted mt-1 truncate">
                    {task.output_path}
                  </p>
                )}
                <p className="text-xs text-foreground-muted mt-1">
                  {new Date(task.created_at).toLocaleString('zh-CN')}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
