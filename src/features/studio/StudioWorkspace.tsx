// src/features/studio/StudioWorkspace.tsx
// 工作台 - 一条主线串起 URL→解析→一键完成→实时进度，开屏即见，取代原先空白的素材库首页
import { useRef, useState } from 'react'
import {
  Sparkles, SlidersHorizontal, Film, Captions, Mic, BookMarked, ShieldAlert,
  CheckCircle2, Loader2, XCircle, CircleDashed, SkipForward, PauseCircle, ChevronRight, Download, FileVideo, X,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { formatDuration } from '@/lib/format'
import { AUTOMATION_STAGE_KEYS, AUTOMATION_STAGE_META } from '@/lib/automationMapper'
import { toLocalVideoSource } from '@/lib/automationPayload'
import { automationApi, videoApi } from '@/lib/api'
import { useParseVideo } from '@/hooks/useParseVideo'
import { useAutoRun } from '@/hooks/useAutoRun'
import { useVideoStore } from '@/stores/videoStore'
import { useAutomationStore } from '@/stores/automationStore'
import { usePrefsStore } from '@/stores/prefsStore'
import { useUiStore, type SettingsSection } from '@/stores/uiStore'
import type { AutomationJob, AutomationStep, AutomationPreferences, VideoParseResult } from '@/types'

/** 字幕处理方式中文文案 */
const SUBTITLE_OP_LABEL: Record<AutomationPreferences['subtitle_operation'], string> = {
  none: '使用原字幕',
  generate: '生成字幕',
  translate: '翻译字幕',
  polish: '润色字幕',
}

/** 阶段状态的视觉样式 */
const STEP_VISUAL: Record<AutomationStep['status'], { ring: string; icon: typeof CheckCircle2 }> = {
  completed: { ring: 'border-success/60 bg-success/15 text-success', icon: CheckCircle2 },
  running: { ring: 'border-info/60 bg-info/15 text-info', icon: Loader2 },
  failed: { ring: 'border-destructive/60 bg-destructive/15 text-destructive', icon: XCircle },
  skipped: { ring: 'border-border bg-muted/40 text-muted-foreground', icon: SkipForward },
  paused: { ring: 'border-warning/60 bg-warning/15 text-warning', icon: PauseCircle },
  cancelled: { ring: 'border-border bg-muted/40 text-muted-foreground', icon: XCircle },
  pending: { ring: 'border-border text-muted-foreground', icon: CircleDashed },
}

interface StudioWorkspaceProps {
  onOpenSettings: (tab?: SettingsSection) => void
}

export function StudioWorkspace({ onOpenSettings }: StudioWorkspaceProps) {
  const [url, setUrl] = useState('')
  const [localVideoPath, setLocalVideoPath] = useState('')
  const [isSelectingLocalVideo, setIsSelectingLocalVideo] = useState(false)
  const [isGeneratingLocalThumbnail, setIsGeneratingLocalThumbnail] = useState(false)
  const localPreviewRequestId = useRef(0)
  const { parse, isParsing } = useParseVideo()
  const { start, isStarting } = useAutoRun()
  const setCurrentVideo = useVideoStore((s) => s.setCurrentVideo)
  const currentVideo = useVideoStore((s) => s.currentVideo)
  const jobs = useAutomationStore((s) => s.jobs)
  const preferences = usePrefsStore((s) => s.preferences)

  // 优先展示进行中的任务，否则展示最近一个
  const activeJob = jobs.find((job) => job.status === 'running' || job.status === 'pending') ?? jobs[0]
  const displayVideo = currentVideo ?? (
    activeJob?.video_info
      ? { ...activeJob.video_info, cover_asset_path: activeJob.cover_asset_path || activeJob.video_info.cover_asset_path || null }
      : null
  )

  const sourceForRun = localVideoPath ? toLocalVideoSource(localVideoPath) : url
  const hasSource = Boolean(sourceForRun.trim())

  const handleUrlChange = (value: string) => {
    setUrl(value)
    if (localVideoPath) {
      setLocalVideoPath('')
      setCurrentVideo(null)
    }
  }

  const handleSelectLocalVideo = async () => {
    if (isSelectingLocalVideo) return
    const picker = window.electron?.dialog?.selectVideoFile
    if (!picker) {
      toast.warning('当前环境不支持选择本地视频，请在桌面应用中使用')
      return
    }

    setIsSelectingLocalVideo(true)
    try {
      const filePath = await picker()
      if (!filePath) return
      const title = filePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || '本地视频'
      setLocalVideoPath(filePath)
      setUrl('')
      const requestId = localPreviewRequestId.current + 1
      localPreviewRequestId.current = requestId
      setCurrentVideo({
        id: 0,
        video_id: 'local-preview',
        platform: 'local',
        title,
        author: '本地视频',
        duration: null,
        thumbnail_url: null,
        formats: [],
        subtitles: [],
      })
      toast.success('已选择本地视频，可直接一键完成')
      setIsGeneratingLocalThumbnail(true)
      void automationApi.previewLocalVideo(filePath)
        .then((result) => {
          if (localPreviewRequestId.current !== requestId) return
          setCurrentVideo(result)
          if (!result.cover_asset_path && !result.thumbnail_url) {
            toast.warning('本地视频已选择，但缩略图生成失败')
          }
        })
        .catch((error) => {
          if (localPreviewRequestId.current !== requestId) return
          toast.warning(`本地视频缩略图生成失败：${error instanceof Error ? error.message : '未知错误'}`)
        })
        .finally(() => {
          if (localPreviewRequestId.current === requestId) {
            setIsGeneratingLocalThumbnail(false)
          }
        })
    } catch (error) {
      toast.error(`选择本地视频失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsSelectingLocalVideo(false)
    }
  }

  const handleClearLocalVideo = () => {
    localPreviewRequestId.current += 1
    setLocalVideoPath('')
    setIsGeneratingLocalThumbnail(false)
    setCurrentVideo(null)
  }

  const handleParse = () => parse(url)
  const handleStart = () => start(sourceForRun)

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      {/* 主操作区 */}
      <Card className="glass">
        <CardContent className="space-y-3 pt-6">
          <div className="flex flex-wrap gap-2">
            <Input
              value={url}
              onChange={(e) => handleUrlChange(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleParse()}
              placeholder="粘贴 YouTube 视频链接…"
              className="h-10 min-w-60 flex-1"
              disabled={Boolean(localVideoPath)}
            />
            <Button variant="outline" className="h-10" onClick={handleParse} disabled={!url.trim() || isParsing}>
              {isParsing ? '解析中…' : '解析'}
            </Button>
            <Button variant="secondary" className="h-10 gap-1.5" onClick={handleSelectLocalVideo} disabled={isSelectingLocalVideo || isStarting}>
              {isSelectingLocalVideo ? <Loader2 className="size-4 animate-spin" /> : <FileVideo className="size-4" />}
              {localVideoPath ? '重新选择本地视频' : '选择本地视频'}
            </Button>
            <AutoRunConfirm
              disabled={!hasSource}
              isStarting={isStarting}
              preferences={preferences}
              onConfirm={handleStart}
              onOpenSettings={onOpenSettings}
            />
          </div>
          {localVideoPath && (
            <div className="flex items-center gap-2 rounded-lg border bg-muted/35 px-3 py-2 text-xs">
              <FileVideo className="size-4 shrink-0 text-primary" />
              <span className="shrink-0 text-foreground">已选择本地视频</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground select-text">{localVideoPath}</span>
              <Button variant="ghost" size="icon" className="size-7 shrink-0" onClick={handleClearLocalVideo}>
                <X className="size-3.5" />
              </Button>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            可以粘贴链接后先「解析」，也可以选择本地视频后直接「一键完成」；两种来源都会进入同一套处理流程。
          </p>
        </CardContent>
      </Card>

      {/* 流程步进器 */}
      <PipelineStepper job={activeJob} />

      {/* 双栏：左信息+进度，右配置摘要 */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          {displayVideo ? <VideoInfoCard video={displayVideo} isThumbnailLoading={Boolean(localVideoPath && isGeneratingLocalThumbnail)} /> : activeJob ? <AutoRunPendingCard job={activeJob} /> : <ParseHint />}
          {activeJob && <JobProgressCard job={activeJob} />}
        </div>
        <ConfigSummary preferences={preferences} onOpenSettings={onOpenSettings} />
      </div>
    </div>
  )
}

/** 一键完成确认（就地 Popover，取代原先跳设置弹窗） */
function AutoRunConfirm({
  disabled,
  isStarting,
  preferences,
  onConfirm,
  onOpenSettings,
}: {
  disabled: boolean
  isStarting: boolean
  preferences: AutomationPreferences
  onConfirm: () => void
  onOpenSettings: (tab?: SettingsSection) => void
}) {
  const [open, setOpen] = useState(false)
  const updatePrefs = usePrefsStore((s) => s.update)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button className="h-10 gap-1.5" disabled={disabled}>
          <Sparkles className="size-4" />
          一键完成
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="glass-strong w-80">
        <div className="space-y-3">
          <div>
            <p className="text-sm font-medium">确认一键完成</p>
            <p className="text-xs text-muted-foreground">可选步骤可在这里临时开关。</p>
          </div>
          <Separator />
          {/* 可选步骤开关：画面处理、配音 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3 rounded-md border p-2.5">
              <div className="min-w-0">
                <p className="text-sm">画面处理</p>
                <p className="text-xs text-muted-foreground">差异化重编码，关闭可明显加快</p>
              </div>
              <Switch checked={preferences.enable_effects} onCheckedChange={(v) => updatePrefs({ enable_effects: v })} />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border p-2.5">
              <div className="min-w-0">
                <p className="text-sm">配音</p>
                <p className="text-xs text-muted-foreground">需先在设置里配置配音渠道</p>
              </div>
              <Switch checked={preferences.enable_voice} onCheckedChange={(v) => updatePrefs({ enable_voice: v })} />
            </div>
          </div>
          <ul className="space-y-1.5 text-xs text-muted-foreground">
            <li className="flex justify-between"><span>字幕</span><span className="text-foreground">{SUBTITLE_OP_LABEL[preferences.subtitle_operation]}{preferences.burn_subtitles ? '·硬字幕' : ''}</span></li>
            <li className="flex justify-between"><span>导出格式</span><span className="text-foreground uppercase">{preferences.output_format}</span></li>
            <li className="flex justify-between"><span>最终导出</span><span className="text-foreground">{preferences.export_with_settings ? '按导出设置' : '直接输出'}</span></li>
          </ul>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" onClick={() => { setOpen(false); onOpenSettings('export') }}>
              打开设置
            </Button>
            <Button
              size="sm"
              className="flex-1"
              disabled={isStarting}
              onClick={() => { onConfirm(); setOpen(false) }}
            >
              {isStarting ? '启动中…' : '确认开始'}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** 六阶段流程步进器 */
function PipelineStepper({ job }: { job?: AutomationJob }) {
  const stepByKey = new Map((job?.steps ?? []).map((step) => [step.key, step]))
  return (
    <div className="glass flex items-center justify-between gap-1 rounded-xl px-4 py-3">
      {AUTOMATION_STAGE_KEYS.map((key, index) => {
        const step = stepByKey.get(key)
        const status = step?.status ?? 'pending'
        const visual = STEP_VISUAL[status]
        const Icon = visual.icon
        return (
          <div key={key} className="flex flex-1 items-center gap-1">
            <div className="flex min-w-0 flex-col items-center gap-1 text-center">
              <span className={cn('grid size-8 place-items-center rounded-full border', visual.ring)}>
                <Icon className={cn('size-4', status === 'running' && 'animate-spin')} />
              </span>
              <span className={cn('truncate text-[11px]', status === 'pending' ? 'text-muted-foreground' : 'text-foreground')}>
                {AUTOMATION_STAGE_META[key].label}
              </span>
            </div>
            {index < AUTOMATION_STAGE_KEYS.length - 1 && (
              <ChevronRight className="size-4 shrink-0 text-muted-foreground/50" />
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 视频信息卡 */
function VideoInfoCard({ video, isThumbnailLoading = false }: { video: VideoParseResult; isThumbnailLoading?: boolean }) {
  const [isDownloadingCover, setIsDownloadingCover] = useState(false)
  const [lastCoverPath, setLastCoverPath] = useState('')
  const previewUrl = video.thumbnail_url || (video.cover_asset_path ? automationApi.mediaUrl(video.cover_asset_path) : '')
  const canDownloadCover = Boolean(video.thumbnail_url)

  const handleDownloadCover = async () => {
    if (isDownloadingCover || !canDownloadCover) return
    const picker = window.electron?.dialog?.selectDirectory
    if (!picker) {
      toast.warning('当前环境不支持系统目录选择，无法打开目录选择窗口')
      return
    }

    try {
      const selectedDir = await picker()
      if (!selectedDir) return
      setIsDownloadingCover(true)
      const result = await videoApi.downloadThumbnail(video.id, undefined, selectedDir)
      setLastCoverPath(result.output_path)
      toast.success('封面已保存')
    } catch (error) {
      toast.error(`封面下载失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsDownloadingCover(false)
    }
  }

  return (
    <Card>
      <CardContent className="flex gap-4 pt-6">
        <div className="aspect-video w-44 shrink-0 overflow-hidden rounded-lg bg-muted">
          {previewUrl
            ? <img src={previewUrl} alt="" className="size-full object-cover" />
            : (
                <div className="grid size-full place-items-center text-muted-foreground">
                  {isThumbnailLoading ? <Loader2 className="size-6 animate-spin" /> : <Film className="size-6" />}
                </div>
              )}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className="line-clamp-2 text-sm font-medium">{video.title ?? '未知标题'}</p>
          <p className="text-xs text-muted-foreground">{video.author ?? '未知作者'}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="secondary">{formatDuration(video.duration)}</Badge>
            <Badge variant="outline">{video.format_count ?? video.formats.length} 个清晰度</Badge>
            <Badge variant="outline">{video.subtitle_count ?? video.subtitles.length} 条字幕轨</Badge>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={handleDownloadCover}
              disabled={isDownloadingCover || !canDownloadCover}
            >
              <Download className="mr-1.5 size-3.5" />
              {isDownloadingCover ? '保存封面中…' : '下载封面'}
            </Button>
          </div>
          <p className="break-all text-[11px] text-muted-foreground">一键完成会自动把封面保存到当前视频项目目录。</p>
          {lastCoverPath && (
            <p className="break-all text-[11px] text-muted-foreground">
              封面已保存：{lastCoverPath}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** 一键完成已启动但前端暂无解析结果时的占位卡片 */
function AutoRunPendingCard({ job }: { job: AutomationJob }) {
  const isWaiting = job.status === 'running' || job.status === 'pending'
  const Icon = isWaiting ? Loader2 : Film
  return (
    <Card className="border-dashed">
      <CardContent className="space-y-2 py-8">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className={cn('size-4 text-info', isWaiting && 'animate-spin')} />
          {isWaiting ? '正在准备视频信息' : '视频信息未加载'}
        </div>
        <p className="text-sm text-muted-foreground">
          {isWaiting ? '一键完成已启动，左侧会在解析完成后显示标题、时长和缩略图。' : '这条历史任务没有可恢复的缩略图和时长信息，可重新解析链接刷新预览。'}
        </p>
        <p className="truncate text-xs text-muted-foreground select-text">{job.source_url}</p>
      </CardContent>
    </Card>
  )
}

/** 解析前的引导提示 */
function ParseHint() {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
        <Sparkles className="size-7 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">粘贴链接或选择本地视频开始</p>
        <p className="text-xs text-muted-foreground/70">链接可先解析预览，本地视频可直接进入一键流程</p>
      </CardContent>
    </Card>
  )
}

/** 当前任务实时进度卡 */
function JobProgressCard({ job }: { job: AutomationJob }) {
  const setWorkspace = useUiStore((s) => s.setWorkspace)
  const jobMessage = job.steps.find((step) => step.error_message)?.error_message
  const statusMessage = job.status === 'cancelled'
    ? (jobMessage || '任务已中断，可在任务队列点击断点续跑')
    : job.status === 'failed'
      ? (jobMessage || '任务失败，请到任务队列查看并重试')
      : job.status === 'paused'
        ? (jobMessage || '任务已暂停，可继续处理')
        : ''
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-sm">
          <span className="truncate">{job.title}</span>
          <Badge variant={job.status === 'failed' ? 'destructive' : job.status === 'completed' ? 'default' : 'secondary'}>
            {job.current_step}
          </Badge>
        </CardTitle>
        <Progress value={job.progress} className="mt-2" />
      </CardHeader>
      <CardContent className="space-y-2">
        {job.steps.map((step) => {
          const visual = STEP_VISUAL[step.status]
          const Icon = visual.icon
          return (
            <div key={step.key} className="flex items-center gap-2.5 text-xs">
              <Icon className={cn('size-4 shrink-0', visual.ring.split(' ').find((c) => c.startsWith('text-')), step.status === 'running' && 'animate-spin')} />
              <span className="w-16 shrink-0 text-foreground">{step.label}</span>
              <span className={cn('min-w-0 flex-1 truncate', step.error_message ? (step.status === 'failed' ? 'text-destructive' : 'text-warning') : 'text-muted-foreground')}>
                {step.error_message || step.description}
              </span>
              {step.status === 'running' && <span className="tabular-nums text-info">{step.progress}%</span>}
            </div>
          )
        })}
        {(job.status === 'failed' || job.status === 'paused' || job.status === 'cancelled') && (
          <div className="mt-3 space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3">
            <p className="text-xs text-warning">{statusMessage}</p>
            <Button variant="outline" size="sm" className="w-full" onClick={() => setWorkspace('queue')}>
              前往任务队列重试或断点续跑
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** 右侧配置摘要 */
function ConfigSummary({
  preferences,
  onOpenSettings,
}: {
  preferences: AutomationPreferences
  onOpenSettings: (tab?: SettingsSection) => void
}) {
  const rows: Array<{ icon: typeof Film; label: string; value: string; tab: SettingsSection }> = [
    { icon: Film, label: '画面处理', value: preferences.enable_effects ? '已开启' : '已关闭', tab: 'effects' },
    { icon: SlidersHorizontal, label: '最终导出', value: preferences.export_with_settings ? '按导出设置' : '直接输出', tab: 'export' },
    { icon: Captions, label: '字幕', value: SUBTITLE_OP_LABEL[preferences.subtitle_operation], tab: 'subtitle' },
    { icon: Mic, label: '配音', value: preferences.enable_voice ? '已开启' : '已关闭', tab: 'voice' },
    { icon: BookMarked, label: '术语字库', value: `${preferences.glossary_terms.length} 条`, tab: 'glossary' },
    { icon: ShieldAlert, label: '禁词', value: `${preferences.banned_words.length} 个`, tab: 'banned' },
  ]
  return (
    <Card className="glass h-fit">
      <CardHeader>
        <CardTitle className="text-sm">本次配置</CardTitle>
        <CardDescription className="text-xs">点任意项可跳转设置调整</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {rows.map((row) => {
          const Icon = row.icon
          return (
            <button
              key={row.label}
              onClick={() => onOpenSettings(row.tab)}
              className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-accent"
            >
              <Icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="flex-1">{row.label}</span>
              <span className="text-xs text-muted-foreground">{row.value}</span>
              <SlidersHorizontal className="size-3.5 text-muted-foreground/50" />
            </button>
          )
        })}
      </CardContent>
    </Card>
  )
}
