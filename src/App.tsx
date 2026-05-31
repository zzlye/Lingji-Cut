// src/App.tsx
// 根组件 - 提供布局壳和全局状态
import { AppShell } from './components/layout/AppShell'

/**
 * 应用根组件
 * 包含布局壳（顶部栏 + 侧边栏 + 主区域 + 日志面板）
 */
export default function App() {
  return <AppShell />
}
