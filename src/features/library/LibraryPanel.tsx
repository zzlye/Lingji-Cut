// src/features/library/LibraryPanel.tsx
// 素材库 - 展示一键流程导出的成品视频
import { useCallback, useEffect, useState } from 'react'
import { Captions, Film, FolderOpen, FolderX, ImageIcon, Play, RefreshCw } from 'lucide-react'
import { automationApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { useAutomationStore } from '@/stores/automationStore'
import { useTaskStore } from '@/stores/taskStore'
import { useUiStore } from '@/stores/uiStore'
import { formatDuration } from '@/lib/format'
import type { AutomationJob } from '@/types'

type ProductItem = {
  job: AutomationJob
  output: string
  kind: 'final' | 'subtitle_only'
  thumbnailSrc: string | null
}

export function LibraryPanel() {
  const jobs = useAutomationStore((s) => s.jobs)
  const removeJob = useAutomationStore((s) => s.removeJob)
  const syncBackendJobs = useAutomationStore((s) => s.syncBackendJobs)
  const openSubtitleWorkbench = useUiStore((s) => s.openSubtitleWorkbench)
  const { addLog } = useTaskStore()
  const [playing, setPlaying] = useState<ProductItem | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ProductItem | null>(null)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [deletingFolderId, setDeletingFolderId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const refreshLibrary = useCallback(async (showLoading = false) => {
    if (showLoading) setRefreshing(true)
    try {
      const backendJobs = await automationApi.listJobs()
      syncBackendJobs(backendJobs)
    } catch (error) {
      addLog('warn', `刷新素材库失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      if (showLoading) setRefreshing(false)
    }
  }, [addLog, syncBackendJobs])

  useEffect(() => {
    refreshLibrary()
    const timer = window.setInterval(() => refreshLibrary(), 8000)
    return () => window.clearInterval(timer)
  }, [refreshLibrary])

  // 取已完成流程的导出阶段产物作为成品
  const products: ProductItem[] = jobs
    .filter((job) => job.status === 'completed')
    .flatMap((job) => {
      const output = job.steps.find((step) => step.key === 'export')?.output_path
      const items: ProductItem[] = output ? [{ job, output, kind: 'final', thumbnailSrc: resolveThumbnailSrc(job) }] : []
      if (job.subtitle_only_video_path) {
        items.push({ job, output: job.subtitle_only_video_path, kind: 'subtitle_only', thumbnailSrc: resolveThumbnailSrc(job) })
      }
      return items
    })

  const handleOpenFolder = async (item: ProductItem) => {
    if (openingId) return
    const { job, output } = item
    setOpeningId(job.id)
    try {
      const localFolder = resolveProductFolderPath(job, output)
      const openLocalPath = window.electron?.shell?.openPath
      if (openLocalPath && localFolder) {
        const error = await openLocalPath(localFolder)
        if (error) throw new Error(error)
        addLog('info', `素材文件夹已打开: ${localFolder}`)
        return
      }
      const result = await automationApi.openJobFolder(job.id)
      addLog('info', `素材文件夹已打开: ${result.folder_path}`)
    } catch (error) {
      addLog('error', `打开素材文件夹失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setOpeningId(null)
    }
  }

  const handleDeleteFolder = async () => {
    if (!deleteTarget || deletingFolderId) return
    const { job } = deleteTarget
    setDeletingFolderId(job.id)
    try {
      const result = await automationApi.deleteJobFolder(job.id)
      removeJob(job.id)
      if (playing?.job.id === job.id) setPlaying(null)
      setDeleteTarget(null)
      addLog('info', `素材文件夹和记录已删除: ${result.folder_path}`)
    } catch (error) {
      addLog('error', `删除素材文件夹失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setDeletingFolderId(null)
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">素材库</h2>
          <p className="text-sm text-muted-foreground">一键流程导出的成品视频会出现在这里。</p>
        </div>
        <Button variant="outline" size="sm" className="h-9" onClick={() => refreshLibrary(true)} disabled={refreshing}>
          <RefreshCw className={`mr-1.5 size-4 ${refreshing ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {products.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
          <Film className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">暂无成品</p>
          <p className="text-xs text-muted-foreground/70">完成一次一键处理后会自动出现在素材库</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {products.map((item) => {
            const { job, output, thumbnailSrc } = item
            const duration = formatDuration(job.video_info?.duration)
            return (
              <Card key={`${job.id}:${item.kind}`} className="overflow-hidden">
                <div className="relative aspect-video bg-muted">
                  {thumbnailSrc ? (
                    <img
                      src={thumbnailSrc}
                      alt=""
                      className="size-full object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="grid size-full place-items-center text-muted-foreground">
                      <ImageIcon className="size-9" />
                    </div>
                  )}
                  <div className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/75 to-transparent p-3">
                    <span className="line-clamp-2 text-sm font-medium text-white">{job.title}</span>
                    <span className="shrink-0 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
                      {duration}
                    </span>
                  </div>
                </div>
                <CardContent className="space-y-3 p-3">
                  <div className="min-h-10 space-y-1">
                    <div className="flex items-center gap-2">
                      {item.kind === 'subtitle_only' && <span className="shrink-0 rounded bg-info/10 px-1.5 py-0.5 text-[11px] text-info">字幕版</span>}
                      <p className="truncate text-xs text-muted-foreground" title={fileNameFromPath(output)}>
                        {fileNameFromPath(output)}
                      </p>
                    </div>
                    <p className="line-clamp-2 break-all text-[11px] leading-4 text-muted-foreground/70 select-text">
                      {resolveProductFolderPath(job, output) || output}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Button size="sm" variant="outline" className="h-9 justify-center" onClick={() => setPlaying(item)}>
                      <Play className="mr-1.5 size-4" />
                      播放
                    </Button>
                    <Button size="sm" variant="secondary" className="h-9 justify-center" onClick={() => openSubtitleWorkbench(job.id)}>
                      <Captions className="mr-1.5 size-4" />
                      字幕调整
                    </Button>
                    <Button size="sm" variant="outline" className="h-9 justify-center" onClick={() => handleOpenFolder(item)} disabled={openingId === job.id}>
                      <FolderOpen className="mr-1.5 size-4" />
                      {openingId === job.id ? '打开中' : '文件夹'}
                    </Button>
                    <Button size="sm" variant="destructive" className="h-9 justify-center" onClick={() => setDeleteTarget(item)} disabled={deletingFolderId === job.id}>
                      <FolderX className="mr-1.5 size-4" />
                      {deletingFolderId === job.id ? '删除中' : '删除'}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      <Dialog open={Boolean(playing)} onOpenChange={(open) => !open && setPlaying(null)}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>{playing?.job.title || '素材预览'}</DialogTitle>
            <DialogDescription className="break-all">{playing?.output}</DialogDescription>
          </DialogHeader>
          {playing && (
            <video
              key={playing.output}
              src={automationApi.mediaUrl(playing.output)}
              className="max-h-[70vh] w-full rounded-lg bg-black"
              controls
              autoPlay
            />
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除整个素材文件夹？</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>这会删除硬盘上的该视频独立文件夹，并同步删除素材库记录。此操作不可恢复。</p>
                <p className="break-all rounded-md bg-muted px-3 py-2 font-mono text-xs text-foreground select-text">
                  {deleteTarget?.output}
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(deletingFolderId)}>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={handleDeleteFolder} disabled={Boolean(deletingFolderId)}>
              {deletingFolderId ? '删除中…' : '确认删除文件夹'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function resolveThumbnailSrc(job: AutomationJob) {
  const coverPath = job.cover_asset_path?.trim()
  if (coverPath) return automationApi.mediaUrl(coverPath)
  return job.video_info?.thumbnail_url || null
}

function fileNameFromPath(path: string) {
  const normalized = path.trim()
  if (!normalized) return ''
  return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized
}

function resolveProductFolderPath(job: AutomationJob, output: string) {
  for (const candidate of [
    output,
    job.source_video_path,
    job.subtitle_asset_path,
    job.voice_asset_path,
    job.subtitle_only_video_path,
    ...job.steps.map((step) => step.output_path || ''),
  ]) {
    const folderPath = folderPathFromMediaPath(candidate || '')
    if (folderPath) return folderPath
  }
  return ''
}

function folderPathFromMediaPath(path: string) {
  const normalized = path.trim()
  if (!normalized) return ''
  const parts = normalized.split(/[\\/]/).filter(Boolean)
  if (parts.length < 2) return ''
  const stageName = parts[parts.length - 2]?.toLowerCase()
  if (stageName && ['downloads', 'output', 'exports'].includes(stageName)) {
    const workspaceParts = parts.slice(0, -2)
    if (workspaceParts.length >= 2 && workspaceParts[workspaceParts.length - 2]?.toLowerCase() === 'videos') {
      return pathPrefix(normalized) + workspaceParts.join('\\')
    }
  }
  return normalized.replace(/[\\/][^\\/]+$/, '')
}

function pathPrefix(path: string) {
  return /^[A-Za-z]:[\\/]/.test(path) ? '' : path.startsWith('\\\\') ? '\\\\' : ''
}
