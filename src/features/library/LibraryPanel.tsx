// src/features/library/LibraryPanel.tsx
// 素材库 - 展示一键流程导出的成品视频
import { useState } from 'react'
import { Film, Play, Trash2 } from 'lucide-react'
import { automationApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
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
  const { addLog } = useTaskStore()
  const [playing, setPlaying] = useState<ProductItem | null>(null)
  // 取已完成流程的导出阶段产物作为成品
  const products: ProductItem[] = jobs
    .filter((job) => job.status === 'completed')
    .flatMap((job) => {
      const output = job.steps.find((step) => step.key === 'export')?.output_path
      return output ? [{ job, output }] : []
    })

  const handleDelete = async (job: AutomationJob) => {
    try {
      await automationApi.deleteJob(job.id)
      removeJob(job.id)
      if (playing?.job.id === job.id) setPlaying(null)
      addLog('info', `素材记录 "${job.title}" 已删除`)
    } catch (error) {
      addLog('error', `删除素材记录失败: ${error instanceof Error ? error.message : '未知错误'}`)
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
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => setPlaying({ job, output })}>
                    <Play className="mr-1.5 size-4" />
                    播放
                  </Button>
                  <Button size="sm" variant="outline" className="text-destructive" onClick={() => handleDelete(job)}>
                    <Trash2 className="mr-1.5 size-4" />
                    删除记录
                  </Button>
                </div>
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
    </div>
  )
}
