// src/features/library/LibraryPanel.tsx
// 素材库 - 展示一键流程导出的成品视频
import { useEffect, useState } from 'react'
import { Film, FolderOpen, FolderX, Play } from 'lucide-react'
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
import type { AutomationJob } from '@/types'

type ProductItem = {
  job: AutomationJob
  output: string
}

export function LibraryPanel() {
  const jobs = useAutomationStore((s) => s.jobs)
  const removeJob = useAutomationStore((s) => s.removeJob)
  const syncBackendJobs = useAutomationStore((s) => s.syncBackendJobs)
  const { addLog } = useTaskStore()
  const [playing, setPlaying] = useState<ProductItem | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ProductItem | null>(null)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [deletingFolderId, setDeletingFolderId] = useState<string | null>(null)

  useEffect(() => {
    automationApi.listJobs()
      .then(syncBackendJobs)
      .catch((error) => addLog('warn', `刷新素材库失败: ${error instanceof Error ? error.message : '未知错误'}`))
  }, [addLog, syncBackendJobs])

  // 取已完成流程的导出阶段产物作为成品
  const products: ProductItem[] = jobs
    .filter((job) => job.status === 'completed')
    .flatMap((job) => {
      const output = job.steps.find((step) => step.key === 'export')?.output_path
      return output ? [{ job, output }] : []
    })

  const handleOpenFolder = async (job: AutomationJob) => {
    if (openingId) return
    setOpeningId(job.id)
    try {
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
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">素材库</h2>
        <p className="text-sm text-muted-foreground">一键流程导出的成品视频会出现在这里。</p>
      </div>

      {products.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
          <Film className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">暂无成品</p>
          <p className="text-xs text-muted-foreground/70">完成一次处理后，导出的视频会显示在这里</p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {products.map(({ job, output }) => (
            <Card key={job.id}>
              <CardContent className="space-y-3 pt-6">
                <div className="flex items-center gap-2">
                  <span className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                    <Film className="size-4" />
                  </span>
                  <p className="min-w-0 flex-1 truncate text-sm font-medium">{job.title}</p>
                </div>
                <p className="break-all text-xs text-muted-foreground select-text">{output}</p>
                <div className="grid grid-cols-3 gap-2">
                  <Button size="sm" className="h-9 w-full justify-center" onClick={() => setPlaying({ job, output })}>
                    <Play className="mr-1.5 size-4" />
                    播放
                  </Button>
                  <Button size="sm" variant="outline" className="h-9 w-full justify-center" onClick={() => handleOpenFolder(job)} disabled={openingId === job.id}>
                    <FolderOpen className="mr-1.5 size-4" />
                    {openingId === job.id ? '打开中…' : '打开文件夹'}
                  </Button>
                  <Button size="sm" variant="destructive" className="h-9 w-full justify-center" onClick={() => setDeleteTarget({ job, output })} disabled={deletingFolderId === job.id}>
                    <FolderX className="mr-1.5 size-4" />
                    {deletingFolderId === job.id ? '删除中…' : '删除文件夹'}
                  </Button>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  删除文件夹会删除硬盘上的该视频文件夹，并同步移出素材库。
                </p>
              </CardContent>
            </Card>
          ))}
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
