// src/features/subtitle/SubtitleWorkbenchPage.tsx
// 独立字幕页面 - 从左侧导航直接进入，集中处理字幕校对、翻译和导出

import { Captions, FilePenLine, Sparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { StudioSubtitleWorkbench } from '@/features/subtitle/StudioSubtitleWorkbench'
import { useAutomationStore } from '@/stores/automationStore'
import { useUiStore } from '@/stores/uiStore'

export function SubtitleWorkbenchPage() {
  const jobs = useAutomationStore((state) => state.jobs)
  const openSettings = useUiStore((state) => state.openSettings)

  // 优先复用当前正在处理的任务字幕，避免用户再手动找路径。
  const activeJob = jobs.find((job) => job.status === 'running' || job.status === 'pending') ?? jobs[0]
  const subtitleStagePath = activeJob?.steps.find((step) => step.key === 'subtitle')?.output_path || ''

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <section className="glass rounded-2xl border p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-primary">
              <Captions className="size-4" />
              独立字幕调整页面
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">字幕校对、AI 处理、单独导出都在这里完成</h1>
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
              这里不绑定工作台流程。你可以单独载入字幕文件，手动逐条校对，也可以只做 AI 翻译、AI 润色或生成文案，再单独保存为 SRT 或 ASS。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">
              <FilePenLine className="mr-1 size-3.5" />
              手动校对
            </Badge>
            <Badge variant="outline">
              <Sparkles className="mr-1 size-3.5" />
              AI 单独处理
            </Badge>
            <Badge variant="outline">独立页面</Badge>
          </div>
        </div>
      </section>

      <StudioSubtitleWorkbench
        suggestedSubtitlePath={isEditableSubtitlePath(subtitleStagePath) ? subtitleStagePath : null}
        onOpenTextSettings={() => openSettings('api')}
      />
    </div>
  )
}

function isEditableSubtitlePath(path: string | null | undefined) {
  return Boolean(path && /\.(srt|vtt|ass)$/i.test(path))
}
