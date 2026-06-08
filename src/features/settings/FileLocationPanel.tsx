// src/features/settings/FileLocationPanel.tsx
// 文件位置设置 - 项目目录、自动化工具状态、子目录预览（从 SettingsCenter 提取并 shadcn 化）
import { useEffect, useMemo, useState } from 'react'
import { FolderOpen, RotateCcw, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { settingsApi } from '@/lib/api'
import { useLogStore } from '@/stores/logStore'
import type { ProjectPaths, ToolStatusMap } from '@/types'

export function FileLocationPanel() {
  const [paths, setPaths] = useState<ProjectPaths | null>(null)
  const [tools, setTools] = useState<ToolStatusMap | null>(null)
  const [projectRoot, setProjectRoot] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isLoadingTools, setIsLoadingTools] = useState(false)
  const addLog = useLogStore((s) => s.addLog)

  const subDirectories = useMemo(() => {
    if (!paths) return []
    return [
      ['下载目录', paths.downloads_dir],
      ['处理中间文件', paths.output_dir],
      ['导出成品', paths.exports_dir],
      ['数据目录', paths.data_dir],
    ] as const
  }, [paths])

  const loadPaths = async () => {
    try {
      const data = await settingsApi.paths()
      setPaths(data)
      setProjectRoot(data.project_root?.path || '')
    } catch (error) {
      addLog('error', `加载文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const loadTools = async () => {
    setIsLoadingTools(true)
    try {
      setTools(await settingsApi.tools())
    } catch (error) {
      addLog('error', `加载工具状态失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsLoadingTools(false)
    }
  }

  useEffect(() => {
    loadPaths()
    loadTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSelectDirectory = async () => {
    const picker = window.electron?.dialog?.selectDirectory
    if (!picker) {
      addLog('warn', '当前环境不支持系统目录选择，请直接输入路径')
      return
    }
    try {
      const selected = await picker(projectRoot)
      if (selected) setProjectRoot(selected)
    } catch (error) {
      addLog('error', `选择目录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const handleSave = async () => {
    if (!projectRoot.trim()) { addLog('warn', '请输入项目目录'); return }
    setIsSaving(true)
    try {
      const data = await settingsApi.updatePaths(projectRoot)
      setPaths(data)
      setProjectRoot(data.project_root.path)
      addLog('info', `项目目录已保存: ${data.project_root.path}`)
    } catch (error) {
      addLog('error', `保存文件位置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  const handleReset = async () => {
    setIsSaving(true)
    try {
      const data = await settingsApi.resetPaths()
      setPaths(data)
      setProjectRoot(data.project_root.path)
      addLog('info', '项目目录已恢复默认')
    } catch (error) {
      addLog('error', `恢复默认目录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">文件位置</h2>
        <p className="text-sm text-muted-foreground">设置一键流程使用的项目目录，保存后自动创建业务子文件夹。</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">项目目录</CardTitle>
          <CardDescription>下载、字幕、配音、中间文件和导出都会按此目录归档。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input value={projectRoot} onChange={(e) => setProjectRoot(e.target.value)} placeholder="例如 D:\视频项目\YouTube" className="h-10 min-w-64 flex-1" />
            <Button variant="outline" className="h-10 gap-1.5" onClick={handleSelectDirectory}><FolderOpen className="size-4" /> 选择文件夹</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button className="h-10" onClick={handleSave} disabled={isSaving}>{isSaving ? '保存中…' : '保存并创建目录'}</Button>
            <Button variant="outline" className="h-10 gap-1.5" onClick={handleReset} disabled={isSaving}><RotateCcw className="size-4" /> 恢复默认</Button>
            <Button variant="ghost" className="h-10 gap-1.5" onClick={loadPaths}><RefreshCw className="size-4" /> 重新读取</Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
            <div>
              <CardTitle className="text-sm">自动化工具</CardTitle>
              <CardDescription>优先读取 D:\tools，找不到回退 PATH。</CardDescription>
            </div>
            <Button variant="outline" size="sm" onClick={loadTools} disabled={isLoadingTools}>{isLoadingTools ? '检测中…' : '检测'}</Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {tools ? Object.entries(tools).map(([key, info]) => (
              <div key={key} className="rounded-md border bg-card px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium">{key === 'yt_dlp' ? 'yt-dlp' : 'ffmpeg'}</span>
                  <Badge variant={info.available ? 'default' : 'destructive'}>{info.available ? `可用 · ${info.source}` : '不可用'}</Badge>
                </div>
                <p className="mt-1 break-all text-xs text-muted-foreground select-text">{info.command}</p>
                {info.error_message && <p className="mt-1 text-[11px] text-destructive">{info.error_message}</p>}
              </div>
            )) : <p className="text-xs text-muted-foreground">正在检测 yt-dlp 和 ffmpeg…</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">自动子目录</CardTitle>
            <CardDescription>保存时创建，后续任务直接使用。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {subDirectories.length > 0 ? subDirectories.map(([label, info]) => (
              <div key={label} className="rounded-md border bg-card px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium">{label}</span>
                  <span className={cn('text-[11px]', info.exists ? 'text-success' : 'text-warning')}>{info.exists ? '已创建' : '保存后创建'}</span>
                </div>
                <p className="mt-1 break-all text-xs text-muted-foreground select-text">{info.path}</p>
              </div>
            )) : <p className="text-xs text-muted-foreground">正在读取项目目录…</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
