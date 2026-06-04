// src/features/library/LibraryPanel.tsx
// 素材库 - 展示一键流程导出的成品视频
import { Film } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { useAutomationStore } from '@/stores/automationStore'

export function LibraryPanel() {
  const jobs = useAutomationStore((s) => s.jobs)
  // 取已完成流程的导出阶段产物作为成品
  const products = jobs
    .filter((job) => job.status === 'completed')
    .map((job) => ({ job, output: job.steps.find((step) => step.key === 'export')?.output_path }))
    .filter((item) => item.output)

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
              <CardContent className="space-y-2 pt-6">
                <div className="flex items-center gap-2">
                  <span className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                    <Film className="size-4" />
                  </span>
                  <p className="min-w-0 flex-1 truncate text-sm font-medium">{job.title}</p>
                </div>
                <p className="break-all text-xs text-muted-foreground select-text">{output}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
