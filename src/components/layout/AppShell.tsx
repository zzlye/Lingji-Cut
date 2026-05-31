// src/components/layout/AppShell.tsx
// 主布局壳组件 - 四区域工作台布局
// 包含：顶部栏 + 左侧边栏 + 中间主区域 + 右侧面板 + 底部日志栏

import { useState } from 'react'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import { LogPanel } from './LogPanel'
import { TaskPanel } from '@/features/tasks/TaskPanel'
import { VideoPreview } from '@/features/download/VideoPreview'
import { LibraryPanel } from '@/features/library/LibraryPanel'
import { HistoryPanel } from '@/features/history/HistoryPanel'
import { EffectsPanel } from '@/features/effects/EffectsPanel'

/** 侧边栏导航项类型 */
export type SidebarItem =
  | 'library'     // 素材库
  | 'tasks'       // 任务队列
  | 'effects'     // 画面处理
  | 'history'     // 历史记录

/**
 * 主布局壳组件
 * 实现四区域工作台布局：顶部栏、左侧边栏、中间主区域、底部日志面板
 */
export function AppShell() {
  // 当前选中的侧边栏项
  const [activeItem, setActiveItem] = useState<SidebarItem>('library')
  // 日志面板是否展开
  const [isLogExpanded, setIsLogExpanded] = useState(false)
  // 右侧预览面板是否折叠
  const [isPreviewCollapsed, setIsPreviewCollapsed] = useState(false)

  return (
    <div className="flex flex-col h-screen bg-background text-foreground overflow-hidden">
      {/* 顶部栏 - URL 输入、解析按钮、全局状态 */}
      <Header />

      {/* 中间内容区域 - 侧边栏 + 主区域 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左侧导航侧边栏 */}
        <Sidebar
          activeItem={activeItem}
          onItemChange={setActiveItem}
        />

        {/* 主内容区域 */}
        <main className="flex-1 overflow-auto">
          <ContentPanel item={activeItem} />
        </main>

        {/* 右侧面板 - 视频预览，可折叠节省参数配置空间 */}
        <aside className={`${isPreviewCollapsed ? 'w-12' : 'w-80'} bg-background-elevated border-l border-border overflow-hidden shrink-0 transition-[width] duration-200`}>
          {isPreviewCollapsed ? (
            <button
              onClick={() => setIsPreviewCollapsed(false)}
              className="w-full h-full flex flex-col items-center justify-start gap-2 py-4 text-foreground-muted hover:text-foreground hover:bg-white/5 transition-colors"
              title="展开预览"
              aria-label="展开预览"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 19l-7-7 7-7" />
              </svg>
              <span className="text-xs [writing-mode:vertical-rl] tracking-[0.2em]">预览</span>
            </button>
          ) : (
            <VideoPreview onCollapse={() => setIsPreviewCollapsed(true)} />
          )}
        </aside>
      </div>

      {/* 底部日志面板 */}
      <LogPanel
        isExpanded={isLogExpanded}
        onToggle={() => setIsLogExpanded(!isLogExpanded)}
      />
    </div>
  )
}

/**
 * 内容面板组件 - 根据侧边栏选择显示不同内容
 */
function ContentPanel({ item }: { item: SidebarItem }) {
  switch (item) {
    case 'library':
      return <LibraryPanel />
    case 'tasks':
      return <TaskPanel />
    case 'effects':
      return <EffectsPanel />
    case 'history':
      return <HistoryPanel />
    default:
      return <LibraryPanel />
  }
}
