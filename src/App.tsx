// src/App.tsx
// 根组件 - 提供布局壳和全局 Toast
import { AppShell } from './components/layout/AppShell'
import { Toaster } from '@/components/ui/sonner'

/**
 * 应用根组件
 * 挂载主布局壳与全局 Toast 通知容器
 */
export default function App() {
  return (
    <>
      <AppShell />
      <Toaster position="bottom-right" richColors closeButton />
    </>
  )
}
