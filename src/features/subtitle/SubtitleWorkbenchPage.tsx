// src/features/subtitle/SubtitleWorkbenchPage.tsx
// 独立字幕页面 - 从左侧导航直接进入，集中处理字幕校对、翻译和导出

import { useEffect, useMemo } from 'react'
import { StudioSubtitleWorkbench } from '@/features/subtitle/StudioSubtitleWorkbench'
import { useAutomationStore } from '@/stores/automationStore'
import { useUiStore } from '@/stores/uiStore'

export function SubtitleWorkbenchPage() {
  const jobs = useAutomationStore((state) => state.jobs)
  const openSettings = useUiStore((state) => state.openSettings)
  const subtitleJobId = useUiStore((state) => state.subtitleJobId)
  const setSubtitleJobId = useUiStore((state) => state.setSubtitleJobId)

  const selectableJobs = useMemo(
    () => jobs.filter((job) => job.id === subtitleJobId || job.subtitle_asset_path || job.source_video_path || job.voice_asset_path),
    [jobs, subtitleJobId],
  )
  const fallbackJob = selectableJobs.find((job) => job.status === 'running' || job.status === 'pending') ?? selectableJobs[0] ?? null
  const selectedJob = selectableJobs.find((job) => job.id === subtitleJobId) ?? fallbackJob

  useEffect(() => {
    if (!selectedJob?.id) return
    if (subtitleJobId !== selectedJob.id) {
      setSubtitleJobId(selectedJob.id)
    }
  }, [selectedJob?.id, setSubtitleJobId, subtitleJobId])

  return (
    <div className="h-full min-h-0 p-3">
      <StudioSubtitleWorkbench
        availableJobs={selectableJobs}
        selectedJob={selectedJob}
        onSelectJob={setSubtitleJobId}
        suggestedSubtitlePath={isEditableSubtitlePath(selectedJob?.subtitle_asset_path) ? selectedJob?.subtitle_asset_path : null}
        onOpenTextSettings={() => openSettings('api')}
      />
    </div>
  )
}

function isEditableSubtitlePath(path: string | null | undefined) {
  return Boolean(path && /\.(srt|vtt|ass)$/i.test(path))
}
