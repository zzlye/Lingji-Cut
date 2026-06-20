// src/features/settings/FileLocationPanel.tsx
// 文件位置设置 - 项目目录、自动化工具状态、视频目录预览（从 SettingsCenter 提取并 shadcn 化）
import { useEffect, useState } from 'react'
import { FolderOpen, RotateCcw, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { settingsApi } from '@/lib/api'
import { useLogStore } from '@/stores/logStore'
import type { ProjectPaths, ToolStatusMap, YtdlpCookieSettings } from '@/types'

export function FileLocationPanel() {
  const [paths, setPaths] = useState<ProjectPaths | null>(null)
  const [tools, setTools] = useState<ToolStatusMap | null>(null)
  const [cookieSettings, setCookieSettings] = useState<YtdlpCookieSettings | null>(null)
  const [projectRoot, setProjectRoot] = useState('')
  const [cookiesFile, setCookiesFile] = useState('')
  const [cookiesBrowser, setCookiesBrowser] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isSavingCookies, setIsSavingCookies] = useState(false)
  const [isLoadingTools, setIsLoadingTools] = useState(false)
  const addLog = useLogStore((s) => s.addLog)

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

  const loadCookieSettings = async () => {
    try {
      const data = await settingsApi.ytdlpCookies()
      setCookieSettings(data)
      setCookiesFile(data.cookies_file || '')
      setCookiesBrowser(data.cookies_browser || '')
    } catch (error) {
      addLog('error', `加载 YouTube Cookies 设置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadPaths()
    loadTools()
    loadCookieSettings()
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

  const handleSelectCookiesFile = async () => {
    const picker = window.electron?.dialog?.selectCookiesFile
    if (!picker) {
      addLog('warn', '当前环境不支持系统文件选择，请直接输入 cookies.txt 路径')
      return
    }
    try {
      const selected = await picker(cookiesFile || projectRoot)
      if (selected) setCookiesFile(selected)
    } catch (error) {
      addLog('error', `选择 Cookies 文件失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const handleSaveCookies = async () => {
    setIsSavingCookies(true)
    try {
      const data = await settingsApi.updateYtdlpCookies({
        cookies_file: cookiesFile,
        cookies_browser: cookiesBrowser,
      })
      setCookieSettings(data)
      setCookiesFile(data.cookies_file || '')
      setCookiesBrowser(data.cookies_browser || '')
      addLog('info', data.cookies_file ? 'YouTube Cookies 设置已保存' : 'YouTube Cookies 文件已清空，将改用浏览器读取')
    } catch (error) {
      addLog('error', `保存 YouTube Cookies 设置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSavingCookies(false)
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
        <p className="text-sm text-muted-foreground">设置一键流程使用的项目目录，保存后只会自动创建 videos 文件夹。</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">项目目录</CardTitle>
          <CardDescription>每个视频都会归档到 videos 里的独立文件夹。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input value={projectRoot} onChange={(e) => setProjectRoot(e.target.value)} placeholder="例如 D:\视频项目\YouTube" className="h-10 min-w-64 flex-1" />
            <Button variant="outline" className="h-10 gap-1.5" onClick={handleSelectDirectory}><FolderOpen className="size-4" /> 选择文件夹</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button className="h-10" onClick={handleSave} disabled={isSaving}>{isSaving ? '保存中…' : '保存'}</Button>
            <Button variant="outline" className="h-10 gap-1.5" onClick={handleReset} disabled={isSaving}><RotateCcw className="size-4" /> 恢复默认</Button>
            <Button variant="ghost" className="h-10 gap-1.5" onClick={loadPaths}><RefreshCw className="size-4" /> 重新读取</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm">YouTube 登录 Cookies</CardTitle>
              <CardDescription>遇到“确认不是机器人”时，优先使用这里选择的 cookies.txt。</CardDescription>
            </div>
            {cookieSettings?.cookies_file ? (
              <Badge variant={cookieSettings.cookies_file_exists ? 'default' : 'destructive'}>
                {cookieSettings.cookies_file_exists ? '文件可用' : '文件不存在'}
              </Badge>
            ) : (
              <Badge variant="outline">未设置文件</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input value={cookiesFile} onChange={(e) => setCookiesFile(e.target.value)} placeholder="选择浏览器扩展导出的 cookies.txt" className="h-10 min-w-64 flex-1" />
            <Button variant="outline" className="h-10 gap-1.5" onClick={handleSelectCookiesFile}><FolderOpen className="size-4" /> 选择文件</Button>
            <Button variant="ghost" className="h-10" onClick={() => setCookiesFile('')}>清空</Button>
          </div>
          <div className="grid gap-2 lg:grid-cols-[1fr_auto]">
            <Input value={cookiesBrowser} onChange={(e) => setCookiesBrowser(e.target.value)} placeholder="浏览器候选，例如 chrome,edge,firefox" className="h-10" />
            <div className="flex flex-wrap gap-2">
              <Button className="h-10" onClick={handleSaveCookies} disabled={isSavingCookies}>{isSavingCookies ? '保存中…' : '保存 Cookies 设置'}</Button>
              <Button variant="ghost" className="h-10 gap-1.5" onClick={loadCookieSettings}><RefreshCw className="size-4" /> 重新读取</Button>
            </div>
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            推荐用浏览器扩展导出 YouTube 登录后的 cookies.txt。只用浏览器读取时，请先完全关闭 Chrome/Edge，否则 yt-dlp 可能无法复制 cookies 数据库。
          </p>
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
            <CardTitle className="text-sm">视频目录</CardTitle>
            <CardDescription>所有视频项目都会放在这里。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {paths?.videos_dir ? (
              <div className="rounded-md border bg-card px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium">videos</span>
                  <span className={paths.videos_dir.exists ? 'text-[11px] text-success' : 'text-[11px] text-warning'}>{paths.videos_dir.exists ? '已创建' : '保存后创建'}</span>
                </div>
                <p className="mt-1 break-all text-xs text-muted-foreground select-text">{paths.videos_dir.path}</p>
              </div>
            ) : <p className="text-xs text-muted-foreground">正在读取项目目录…</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
