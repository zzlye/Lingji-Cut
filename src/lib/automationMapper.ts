// src/lib/automationMapper.ts
// 后端自动化任务 ↔ 前端展示结构的统一映射，收敛 Header/TaskPanel 原先各写一份的重复逻辑
import type { AutomationJob, AutomationStep, BackendAutomationJob } from '@/types'

/** 自动流程六阶段顺序 */
export const AUTOMATION_STAGE_KEYS: AutomationStep['key'][] = ['parse', 'download', 'effects', 'subtitle', 'voice', 'export']

/** 各阶段的标签、描述和「当前步骤」文案匹配关键字 */
export const AUTOMATION_STAGE_META: Record<AutomationStep['key'], { label: string; description: string; keyword: string }> = {
  parse: { label: '解析视频', description: '读取 YouTube 元数据和字幕轨', keyword: '解析' },
  download: { label: '下载入库', description: '下载原视频并归档到项目目录', keyword: '下载' },
  effects: { label: '画面处理', description: '应用画面差异化和输出参数', keyword: '画面' },
  subtitle: { label: '字幕处理', description: '生成、翻译、润色并渲染字幕', keyword: '字幕' },
  voice: { label: '配音生成', description: '按配置生成或跳过配音', keyword: '配音' },
  export: { label: '合成导出', description: '合成视频、字幕、配音并导出成品', keyword: '导出' },
}

/** 把后端持久化自动任务转换为前端展示结构 */
export function mapBackendAutomationJob(job: BackendAutomationJob): AutomationJob {
  const steps: AutomationStep[] = AUTOMATION_STAGE_KEYS.map((key) => {
    const stage = job.stages.find((item) => item.key === key)
    // 后端只在阶段记录里给出明确状态；运行中阶段靠 current_step 文案推断
    const isCurrentStep = job.status === 'running' && Boolean(job.current_step?.includes(AUTOMATION_STAGE_META[key].keyword))
    const status: AutomationStep['status'] =
      stage?.status === 'completed' || stage?.status === 'failed' || stage?.status === 'skipped' || stage?.status === 'paused' || stage?.status === 'cancelled'
        ? stage.status
        : isCurrentStep || stage?.status === 'running'
          ? 'running'
          : 'pending'
    return {
      key,
      label: AUTOMATION_STAGE_META[key].label,
      description: AUTOMATION_STAGE_META[key].description,
      status,
      progress: stage?.status === 'completed' || stage?.status === 'skipped' ? 100 : Math.round(stage?.progress || 0),
      output_path: stage?.output_path || null,
      error_message: stage?.error_message || null,
    }
  })

  return {
    id: job.id,
    title: job.title || '一键自动流程',
    source_url: job.source_url,
    video_id: job.video_id,
    video_info: job.video_info || null,
    status: job.status,
    progress: Math.round(job.progress || 0),
    current_step: job.current_step || '等待开始',
    batch_id: job.batch_id,
    created_at: job.created_at || new Date().toISOString(),
    completed_at: job.completed_at,
    steps,
    can_pause: job.can_pause,
    can_cancel: job.can_cancel,
    can_resume: job.can_resume,
    can_retry: job.can_retry,
    subtitle_asset_path: job.subtitle_asset_path || null,
    source_subtitle_path: job.source_subtitle_path || null,
    translated_subtitle_path: job.translated_subtitle_path || null,
    source_video_path: job.source_video_path || null,
    voice_asset_path: job.voice_asset_path || null,
    cover_asset_path: job.cover_asset_path || null,
  }
}

/** 批次聚合摘要 */
export type BatchSummary = {
  batchId: string
  total: number
  running: number
  pending: number
  paused: number
  cancelled: number
  completed: number
  failed: number
  progress: number
}

/** 按批次聚合自动任务，用于批量暂停和恢复 */
export function collectBatchSummaries(jobs: AutomationJob[]): BatchSummary[] {
  const map = new Map<string, BatchSummary>()
  for (const job of jobs) {
    if (!job.batch_id) continue
    const summary = map.get(job.batch_id) || {
      batchId: job.batch_id,
      total: 0, running: 0, pending: 0, paused: 0, cancelled: 0, completed: 0, failed: 0, progress: 0,
    }
    summary.total += 1
    summary.progress += job.progress || 0
    summary[job.status] += 1
    map.set(job.batch_id, summary)
  }
  return Array.from(map.values()).map((summary) => ({
    ...summary,
    progress: summary.total ? Math.round(summary.progress / summary.total) : 0,
  }))
}
