// src/features/studio/StudioWorkspace.tsx
// 工作台 - 一条主线串起 URL→解析→一键完成→实时进度，开屏即见，取代原先空白的素材库首页
import { useState } from 'react'
import {
  Sparkles, SlidersHorizontal, Film, Captions, Mic, BookMarked, ShieldAlert,
  CheckCircle2, Loader2, XCircle, CircleDashed, SkipForward, PauseCircle, ChevronRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { formatDuration } from '@/lib/format'
import { AUTOMATION_STAGE_KEYS, AUTOMATION_STAGE_META } from '@/lib/automationMapper'
import { useParseVideo } from '@/hooks/useParseVideo'
import { useAutoRun } from '@/hooks/useAutoRun'
import { useVideoStore } from '@/stores/videoStore'
import { useAutomationStore } from '@/stores/automationStore'
import { usePrefsStore } from '@/stores/prefsStore'
import type { SettingsSection } from '@/stores/uiStore'
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
  const { parse, isParsing } = useParseVideo()
  const { start, isStarting } = useAutoRun()
  const currentVideo = useVideoStore((s) => s.currentVideo)
  const jobs = useAutomationStore((s) => s.jobs)
  const preferences = usePrefsStore((s) => s.preferences)

  // 优先展示进行中的任务，否则展示最近一个
  const activeJob = jobs.find((job) => job.status === 'running' || job.status === 'pending') ?? jobs[0]

  const handleParse = () => parse(url)
  const handleStart = () => start(url)

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      {/* 主操作区 */}
      <Card className="glass">
        <CardContent className="space-y-3 pt-6">
          <div className="flex flex-wrap gap-2">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleParse()}
              placeholder="粘贴 YouTube 视频链接…"
              className="h-10 min-w-60 flex-1"
            />
            <Button variant="outline" className="h-10" onClick={handleParse} disabled={!url.trim() || isParsing}>
              {isParsing ? '解析中…' : '解析'}
            </Button>
            <AutoRunConfirm
              disabled={!url.trim()}
              isStarting={isStarting}
              preferences={preferences}
              onConfirm={handleStart}
              onOpenSettings={onOpenSettings}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            粘贴链接后点「解析」预览信息，或直接「一键完成」自动跑完 解析 → 下载 → 画面处理 → 字幕 → 配音 → 导出。
          </p>
        </CardContent>
      </Card>

      {/* 流程步进器 */}
      <PipelineStepper job={activeJob} />

      {/* 双栏：左信息+进度，右配置摘要 */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          {currentVideo ? <VideoInfoCard video={currentVideo} /> : <ParseHint />}
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
            <p className="text-xs text-muted-foreground">将按当前设置自动跑完整条流水线。</p>
          </div>
          <Separator />
          <ul className="space-y-1.5 text-xs text-muted-foreground">
            <li className="flex justify-between"><span>画面处理</span><span className="text-foreground">{preferences.enable_effects ? '开启' : '关闭'}</span></li>
            <li className="flex justify-between"><span>字幕</span><span className="text-foreground">{SUBTITLE_OP_LABEL[preferences.subtitle_operation]}{preferences.burn_subtitles ? '·硬字幕' : ''}</span></li>
            <li className="flex justify-between"><span>配音</span><span className="text-foreground">{preferences.enable_voice ? (preferences.voice_mode === 'segmented' ? '分段配音' : '整段配音') : '关闭'}</span></li>
            <li className="flex justify-between"><span>导出格式</span><span className="text-foreground uppercase">{preferences.output_format}</span></li>
          </ul>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" onClick={() => { setOpen(false); onOpenSettings('effects') }}>
              调整设置
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
function VideoInfoCard({ video }: { video: VideoParseResult }) {
  return (
    <Card>
      <CardContent className="flex gap-4 pt-6">
        <div className="aspect-video w-44 shrink-0 overflow-hidden rounded-lg bg-muted">
          {video.thumbnail_url
            ? <img src={video.thumbnail_url} alt="" className="size-full object-cover" />
            : <div className="grid size-full place-items-center text-muted-foreground"><Film className="size-6" /></div>}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className="line-clamp-2 text-sm font-medium">{video.title ?? '未知标题'}</p>
          <p className="text-xs text-muted-foreground">{video.author ?? '未知作者'}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="secondary">{formatDuration(video.duration)}</Badge>
            <Badge variant="outline">{video.formats.length} 个清晰度</Badge>
            <Badge variant="outline">{video.subtitles.length} 条字幕轨</Badge>
          </div>
        </div>
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
        <p className="text-sm text-muted-foreground">粘贴 YouTube 链接开始</p>
        <p className="text-xs text-muted-foreground/70">解析后在此预览视频信息，并可逐步处理或一键完成</p>
      </CardContent>
    </Card>
  )
}

/** 当前任务实时进度卡 */
function JobProgressCard({ job }: { job: AutomationJob }) {
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
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {step.error_message || step.description}
              </span>
              {step.status === 'running' && <span className="tabular-nums text-info">{step.progress}%</span>}
            </div>
          )
        })}
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
