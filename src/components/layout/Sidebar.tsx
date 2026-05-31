// src/components/layout/Sidebar.tsx
// 左侧导航侧边栏组件 - 素材库、任务队列、历史记录

import type { SidebarItem } from './AppShell'

/** 侧边栏属性 */
interface SidebarProps {
  /** 当前选中的项 */
  activeItem: SidebarItem
  /** 选项变更回调 */
  onItemChange: (item: SidebarItem) => void
}

/** 导航项配置 */
const NAV_ITEMS: Array<{
  id: SidebarItem
  label: string
  icon: React.ReactNode
}> = [
  {
    id: 'library',
    label: '素材库',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
      </svg>
    )
  },
  {
    id: 'tasks',
    label: '任务队列',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
    )
  },
  {
    id: 'history',
    label: '历史记录',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    )
  }
]

/**
 * 左侧导航侧边栏
 * 显示功能模块导航，支持高亮当前选中项
 */
export function Sidebar({ activeItem, onItemChange }: SidebarProps) {
  return (
    <nav className="w-16 bg-background-elevated border-r border-border flex flex-col items-center py-3 gap-1 shrink-0">
      {NAV_ITEMS.map((item) => (
        <button
          key={item.id}
          onClick={() => onItemChange(item.id)}
          className={`
            w-12 h-12 flex flex-col items-center justify-center rounded-lg transition-colors
            ${activeItem === item.id
              ? 'bg-primary/20 text-primary'
              : 'text-foreground-muted hover:bg-white/5 hover:text-foreground'
            }
          `}
          title={item.label}
        >
          {item.icon}
          <span className="text-[10px] mt-0.5 leading-tight">{item.label}</span>
        </button>
      ))}
    </nav>
  )
}
