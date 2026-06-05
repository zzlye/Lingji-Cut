// src/features/subtitle/StudioSubtitleWorkbench.tsx
// 工作台字幕调整区 - 支持读取字幕文件、逐条手动校对、AI 润色/翻译/生成，并保留时间轴

import { useEffect, useMemo, useState } from 'react'
import { Bot, Captions, FileText, Film, History, Languages, ListChecks, Settings2, Sparkles, Volume2, Wand2 } from 'lucide-react'
import { subtitleApi, profileApi, automationApi } from '@/lib/api'
import { useAutomationStore } from '@/stores/automationStore'
import { useTaskStore } from '@/stores/taskStore'
import { usePrefsStore } from '@/stores/prefsStore'
import type { ApiProfile, AutomationJob, SubtitleEntry, SubtitleTextOperation } from '@/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { TextField, TextareaField, SelectField, SegmentedField, type FieldOption } from '@/components/fields'

const SAMPLE_SUBTITLE_TEXT = `1
00:00:01,000 --> 00:00:03,000
这里可以粘贴 SRT 或 VTT 字幕文本。

2
00:00:03,200 --> 00:00:05,400
解析后就能逐条手动校对，也能单独做 AI 翻译或润色。`

const TARGET_LANG_OPTIONS: FieldOption[] = [['zh-CN', '中文 简体'], ['en', '英文'], ['ja', '日文'], ['ko', '韩文'], ['es', '西班牙语']]
const AI_SCOPE_OPTIONS: FieldOption[] = [['all', '全部字幕'], ['current', '当前一条']]
const AI_OPERATION_LABEL: Record<Exclude<SubtitleTextOperation, 'none'>, string> = {
  polish: 'AI 润色',
  translate: 'AI 翻译',
  generate: 'AI 生成文案',
}
const JOB_STATUS_LABEL: Record<AutomationJob['status'], string> = {
  pending: '等待中',
  running: '运行中',
  paused: '已暂停',
  cancelled: '已取消',
  completed: '已完成',
  failed: '失败',
}

type CorrectionNotice = {
  type: 'info' | 'success' | 'warning' | 'error'
  message: string
} | null

interface StudioSubtitleWorkbenchProps {
  suggestedSubtitlePath?: string | null
  availableJobs?: AutomationJob[]
  selectedJob?: AutomationJob | null
  onSelectJob?: (jobId: string | null) => void
  onOpenTextSettings?: () => void
}

export function StudioSubtitleWorkbench({
  suggestedSubtitlePath,
  availableJobs = [],
  selectedJob = null,
  onSelectJob,
  onOpenTextSettings,
}: StudioSubtitleWorkbenchProps) {
  const preferences = usePrefsStore((state) => state.preferences)
  const updatePreferences = usePrefsStore((state) => state.update)
  const { addLog } = useTaskStore()
  const syncBackendJob = useAutomationStore((state) => state.syncBackendJob)

  const [subtitlePath, setSubtitlePath] = useState(suggestedSubtitlePath || '')
  const [pasteText, setPasteText] = useState(SAMPLE_SUBTITLE_TEXT)
  const [pasteFormat, setPasteFormat] = useState<'srt' | 'vtt'>('srt')
  const [entries, setEntries] = useState<SubtitleEntry[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [outputPath, setOutputPath] = useState('')
  const [fileName, setFileName] = useState('manual_subtitle.srt')
  const [lastSavedPath, setLastSavedPath] = useState('')
  const [notice, setNotice] = useState<CorrectionNotice>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [textProfiles, setTextProfiles] = useState<ApiProfile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(preferences.text_profile_id)
  const [targetLanguage, setTargetLanguage] = useState(preferences.subtitle_target_language || 'zh-CN')
  const [aiScope, setAiScope] = useState<'all' | 'current'>('all')
  const [isAiProcessing, setIsAiProcessing] = useState(false)
  const [activeAiLabel, setActiveAiLabel] = useState('')
  const [isReExporting, setIsReExporting] = useState(false)

  const selectedEntry = entries[selectedIndex] || null
  const plainText = useMemo(
    () => entries.map((entry) => entry.text.trim()).filter(Boolean).join('\n'),
    [entries],
  )
  const totalChars = useMemo(
    () => entries.reduce((sum, entry) => sum + entry.text.replace(/\s/g, '').length, 0),
    [entries],
  )
  const invalidCount = useMemo(
    () => entries.filter((entry) => timeToMs(entry.end) <= timeToMs(entry.start)).length,
    [entries],
  )
  const suggestedSubtitleFile = useMemo(
    () => {
      const value = (suggestedSubtitlePath || '').trim()
      return /\.(srt|vtt|ass)$/i.test(value) ? value : ''
    },
    [suggestedSubtitlePath],
  )
  const selectedJobSubtitleFile = useMemo(
    () => {
      const value = (selectedJob?.subtitle_asset_path || '').trim()
      return /\.(srt|vtt|ass)$/i.test(value) ? value : ''
    },
    [selectedJob?.subtitle_asset_path],
  )
  const selectedJobSourceVideo = (selectedJob?.source_video_path || '').trim()
  const selectedJobVoicePath = (selectedJob?.voice_asset_path || '').trim()
  const jobOptions = useMemo<FieldOption[]>(
    () => [
      ['', '不绑定任务，仅手动处理'],
      ...availableJobs.map((job) => [job.id, `${JOB_STATUS_LABEL[job.status]} · ${job.title}`] as FieldOption),
    ],
    [availableJobs],
  )
  const selectedProfile = textProfiles.find((profile) => profile.id === selectedProfileId) || null

  useEffect(() => {
    if (!subtitlePath && suggestedSubtitleFile) {
      setSubtitlePath(suggestedSubtitleFile)
    }
  }, [subtitlePath, suggestedSubtitleFile])

  useEffect(() => {
    const loadTextProfiles = async () => {
      try {
        const profiles = await profileApi.listText()
        setTextProfiles(profiles)
        if (!profiles.length) return
        const preferred = profiles.find((profile) => profile.id === preferences.text_profile_id) || profiles[0]
        setSelectedProfileId((current) => current || preferred.id)
      } catch (error) {
        addLog('error', `加载文本 API 配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
      }
    }
    loadTextProfiles()
  }, [addLog, preferences.text_profile_id])

  const applyParsedEntries = (nextEntries: SubtitleEntry[], message: string, path?: string) => {
    const normalized = normalizeEntries(nextEntries)
    setEntries(normalized)
    setSelectedIndex(0)
    setNotice({ type: 'success', message })
    if (path) {
      setSubtitlePath(path)
      setOutputPath(path.toLowerCase().endsWith('.srt') ? path : '')
      setFileName(path.split(/[\\/]/).pop() || 'manual_subtitle.srt')
    }
  }

  const handleParseFile = async (pathOverride?: string) => {
    const rawPath = (pathOverride ?? subtitlePath).trim()
    if (!rawPath) {
      setNotice({ type: 'warning', message: '请先填写 SRT / VTT / ASS 字幕文件路径。' })
      return
    }

    setIsLoading(true)
    setNotice({ type: 'info', message: '正在读取字幕文件...' })
    try {
      const result = await subtitleApi.parseFile(rawPath)
      applyParsedEntries(result.entries, result.message, result.output_path)
      if (result.output_path) {
        setFileName(result.output_path.split(/[\\/]/).pop() || 'manual_subtitle.srt')
      }
      addLog('info', result.message)
    } catch (error) {
      const message = `读取字幕失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsLoading(false)
    }
  }

  const handleParseText = async () => {
    if (!pasteText.trim()) {
      setNotice({ type: 'warning', message: '请先粘贴 SRT 或 VTT 字幕文本。' })
      return
    }

    setIsLoading(true)
    setNotice({ type: 'info', message: '正在解析字幕文本...' })
    try {
      const result = await subtitleApi.parseText({ content: pasteText, format: pasteFormat })
      applyParsedEntries(result.entries, result.message)
      addLog('info', result.message)
    } catch (error) {
      const message = `解析字幕失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsLoading(false)
    }
  }

  const updateEntry = <K extends keyof SubtitleEntry>(index: number, key: K, value: SubtitleEntry[K]) => {
    setEntries((current) => normalizeEntries(current.map((entry, itemIndex) => (
      itemIndex === index ? { ...entry, [key]: value } : entry
    ))))
  }

  const addEntry = () => {
    const previous = entries[entries.length - 1]
    const start = previous?.end || '00:00:00,000'
    const end = msToTime(timeToMs(start) + 2200)
    const nextEntries = normalizeEntries([
      ...entries,
      { index: entries.length + 1, start, end, text: '新字幕' },
    ])
    setEntries(nextEntries)
    setSelectedIndex(nextEntries.length - 1)
  }

  const removeEntry = (index: number) => {
    const nextEntries = normalizeEntries(entries.filter((_, itemIndex) => itemIndex !== index))
    setEntries(nextEntries)
    setSelectedIndex(Math.max(0, Math.min(index, nextEntries.length - 1)))
  }

  const cleanupEntries = () => {
    const nextEntries = normalizeEntries(entries
      .map((entry) => ({ ...entry, text: entry.text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).join('\n') }))
      .filter((entry) => entry.text.trim()))
    setEntries(nextEntries)
    setSelectedIndex(Math.min(selectedIndex, Math.max(0, nextEntries.length - 1)))
    setNotice({ type: 'success', message: `已清理字幕，保留 ${nextEntries.length} 条。` })
  }

  const handleSelectProfile = (value: string) => {
    const id = Number(value) || null
    setSelectedProfileId(id)
    updatePreferences({ text_profile_id: id })
  }

  const handleTargetLanguageChange = (value: string) => {
    setTargetLanguage(value)
    updatePreferences({ subtitle_target_language: value })
  }

  const handleAiProcess = async (operation: Exclude<SubtitleTextOperation, 'none'>) => {
    if (!selectedProfileId) {
      setNotice({ type: 'warning', message: '请先选择一个可用的文本 API 配置。' })
      return
    }
    if (!entries.length) {
      setNotice({ type: 'warning', message: '请先读取字幕文件或解析字幕文本。' })
      return
    }
    if (aiScope === 'current' && !selectedEntry) {
      setNotice({ type: 'warning', message: '当前没有选中的字幕条目。' })
      return
    }

    const targetIndexes = aiScope === 'current' ? [selectedIndex] : entries.map((_, index) => index)
    const targetEntries = targetIndexes.map((index) => entries[index])
    const actionLabel = AI_OPERATION_LABEL[operation]

    setIsAiProcessing(true)
    setActiveAiLabel(actionLabel)
    setNotice({ type: 'info', message: `${actionLabel}处理中...` })
    try {
      const result = await subtitleApi.processEntries({
        entries: targetEntries,
        profile_id: selectedProfileId,
        operation,
        target_language: operation === 'translate' ? targetLanguage : undefined,
      })

      setEntries((current) => {
        const next = [...current]
        result.entries.forEach((entry, itemIndex) => {
          const targetIndex = targetIndexes[itemIndex]
          if (targetIndex === undefined) return
          next[targetIndex] = { ...next[targetIndex], start: entry.start, end: entry.end, text: entry.text }
        })
        return normalizeEntries(next)
      })
      setNotice({ type: 'success', message: `${actionLabel}完成，已更新 ${result.entries.length} 条字幕。` })
      addLog('info', `${actionLabel}完成，范围：${aiScope === 'all' ? '全部字幕' : '当前一条'}`)
    } catch (error) {
      const message = `AI 处理失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsAiProcessing(false)
      setActiveAiLabel('')
    }
  }

  const saveSrt = async () => {
    if (!canSave(entries, invalidCount, setNotice)) return

    setIsSaving(true)
    setNotice({ type: 'info', message: '正在保存 SRT 字幕...' })
    try {
      const sourcePath = pickSubtitleWorkspaceSource(
        outputPath.trim(),
        subtitlePath.trim(),
        selectedJobSubtitleFile,
        selectedJobSourceVideo,
        lastSavedPath,
      )
      const result = await subtitleApi.saveCorrected({
        entries,
        output_path: outputPath.trim() || undefined,
        file_name: fileName.trim() || undefined,
        format: 'srt',
        source_path: sourcePath,
      })
      setLastSavedPath(result.output_path)
      setOutputPath(result.output_path)
      setEntries(normalizeEntries(result.entries))
      setNotice({ type: 'success', message: result.message })
      addLog('info', `字幕已保存: ${result.output_path}`)
    } catch (error) {
      const message = `保存 SRT 失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsSaving(false)
    }
  }

  const saveAss = async () => {
    if (!canSave(entries, invalidCount, setNotice)) return

    setIsSaving(true)
    setNotice({ type: 'info', message: '正在生成 ASS 字幕...' })
    try {
      const sourcePath = pickSubtitleWorkspaceSource(
        outputPath.trim(),
        subtitlePath.trim(),
        selectedJobSubtitleFile,
        selectedJobSourceVideo,
        lastSavedPath,
      )
      const result = await subtitleApi.saveAss({
        entries,
        file_name: fileName.replace(/\.(srt|vtt|ass)$/i, '.ass'),
        preset_id: preferences.subtitle_preset_id,
        source_path: sourcePath,
      })
      setLastSavedPath(result.output_path)
      setNotice({ type: 'success', message: result.message })
      addLog('info', `ASS 字幕已生成: ${result.output_path}`)
    } catch (error) {
      const message = `生成 ASS 失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsSaving(false)
    }
  }

  const handleSelectJob = (jobId: string) => {
    onSelectJob?.(jobId || null)
  }

  const ensureAssForReExport = async () => {
    const sourcePath = pickSubtitleWorkspaceSource(
      outputPath.trim(),
      subtitlePath.trim(),
      selectedJobSubtitleFile,
      selectedJobSourceVideo,
      lastSavedPath,
    )
    const result = await subtitleApi.saveAss({
      entries,
      output_path: buildAssOutputPath(outputPath.trim()),
      file_name: normalizeAssFileName(fileName.trim() || 'manual_subtitle.ass'),
      preset_id: preferences.subtitle_preset_id,
      source_path: sourcePath,
    })
    setLastSavedPath(result.output_path)
    addLog('info', `重新导出使用 ASS: ${result.output_path}`)
    return result.output_path
  }

  const reExport = async () => {
    if (!selectedJob?.id) {
      setNotice({ type: 'warning', message: '请先从任务队列或历史记录里选择一个任务。' })
      return
    }
    if (selectedJob.status === 'running' || selectedJob.status === 'pending') {
      setNotice({ type: 'warning', message: '当前任务还在执行中，请等它结束后再重新导出。' })
      return
    }
    if (!selectedJobSourceVideo) {
      setNotice({ type: 'warning', message: '这个任务没有可复用的源视频，暂时不能重新导出。' })
      return
    }
    if (!canSave(entries, invalidCount, setNotice)) return

    setIsReExporting(true)
    setNotice({ type: 'info', message: '正在生成 ASS 并重新合成导出...' })
    try {
      const assPath = await ensureAssForReExport()
      const result = await automationApi.reExport(selectedJob.id, {
        subtitle_path: assPath,
        output_format: preferences.output_format,
        export_with_settings: preferences.export_with_settings,
        export_settings: preferences.export_settings,
        audio_mode: preferences.audio_mode,
        original_volume: preferences.original_volume,
      })
      syncBackendJob(await automationApi.getJob(selectedJob.id))
      setNotice({ type: 'success', message: result.message })
      addLog('info', `重新导出完成: ${result.output_path}`)
    } catch (error) {
      const message = `重新导出失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsReExporting(false)
    }
  }

  return (
    <Card className="glass">
      <CardHeader className="border-b">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Captions className="size-4" />
              字幕调整台
            </CardTitle>
            <CardDescription className="mt-1">
              在这里单独做字幕手动校对，也可以单独执行 AI 润色、AI 翻译或 AI 生成文案。
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">手动校对</Badge>
            <Badge variant="outline">AI 单独处理</Badge>
            <Badge variant="outline">保留时间轴</Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 p-4">
        {notice && <NoticeBox notice={notice} />}

        <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)_300px]">
          <div className="space-y-4">
            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="任务来源" description="可直接从任务队列或历史记录挑一个任务，把字幕载进来继续改，改完再重新合并导出。" />
              <div className="mt-3 space-y-3">
                <SelectField
                  label="选择任务"
                  value={selectedJob?.id || ''}
                  options={jobOptions}
                  onChange={handleSelectJob}
                  description={availableJobs.length ? '这里只列出已有字幕、源视频或配音产物的任务。' : '当前还没有可复用的一键流程任务。'}
                  placeholder={availableJobs.length ? '选择一个任务' : '暂无可选任务'}
                />
                {selectedJob ? (
                  <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">
                        {selectedJob.status === 'completed' || selectedJob.status === 'failed' || selectedJob.status === 'cancelled' ? <History className="mr-1 size-3.5" /> : <ListChecks className="mr-1 size-3.5" />}
                        {selectedJob.status === 'completed' || selectedJob.status === 'failed' || selectedJob.status === 'cancelled' ? '历史任务' : '队列任务'}
                      </Badge>
                      <Badge variant="outline">{JOB_STATUS_LABEL[selectedJob.status]}</Badge>
                    </div>
                    <p className="mt-2 font-medium text-foreground">{selectedJob.title}</p>
                    <div className="mt-3 space-y-2 text-muted-foreground">
                      <PathRow icon={FileText} label="可编辑字幕" path={selectedJobSubtitleFile} emptyText="这个任务还没找到可编辑字幕文件" />
                      <PathRow icon={Film} label="重导出源视频" path={selectedJobSourceVideo} emptyText="没有可复用源视频" />
                      <PathRow icon={Volume2} label="配音音轨" path={selectedJobVoicePath} emptyText="没有配音音轨，将只合成视频和字幕" />
                    </div>
                    <Button variant="outline" size="sm" className="mt-3 w-full" onClick={() => handleParseFile(selectedJobSubtitleFile)} disabled={!selectedJobSubtitleFile || isLoading}>
                      载入这个任务的字幕
                    </Button>
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
                    当前不绑定任务。你也可以手动填写字幕路径或直接粘贴字幕文本。
                  </div>
                )}
              </div>
            </section>

            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="导入字幕" description="支持 SRT / VTT / ASS 文件，也可以直接粘贴 SRT / VTT 文本。" />
              <div className="mt-3 space-y-3">
                <TextField label="字幕文件路径" value={subtitlePath} placeholder="D:\\项目\\output\\subtitle.ass" onChange={setSubtitlePath} />
                <Button className="w-full" onClick={() => handleParseFile()} disabled={isLoading}>
                  {isLoading ? '读取中…' : '读取字幕文件'}
                </Button>
                {suggestedSubtitleFile && (
                  <div className="rounded-lg border border-primary/20 bg-primary/5 p-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <FileText className="size-4 text-primary" />
                      当前任务字幕文件
                    </div>
                    <p className="mt-2 break-all text-xs text-muted-foreground">{suggestedSubtitleFile}</p>
                    <Button variant="outline" size="sm" className="mt-3 w-full" onClick={() => handleParseFile(suggestedSubtitleFile)} disabled={isLoading}>
                      载入当前任务字幕
                    </Button>
                  </div>
                )}
              </div>
            </section>

            <section className="rounded-xl border bg-background/60 p-4">
              <div className="flex items-start justify-between gap-3">
                <SectionTitle title="粘贴解析" description="适合把外部字幕或人工整理后的 SRT / VTT 直接贴进来。" />
                <select
                  value={pasteFormat}
                  onChange={(event) => setPasteFormat(event.target.value as 'srt' | 'vtt')}
                  className="h-8 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-primary"
                >
                  <option value="srt">SRT</option>
                  <option value="vtt">VTT</option>
                </select>
              </div>
              <TextareaField label="字幕文本" value={pasteText} rows={11} onChange={setPasteText} />
              <Button variant="outline" className="mt-3 w-full" onClick={handleParseText} disabled={isLoading}>
                解析字幕文本
              </Button>
            </section>
          </div>

          <section className="min-h-[640px] rounded-xl border bg-background/60">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
              <div>
                <h3 className="text-sm font-medium">逐条校对</h3>
                <p className="mt-1 text-xs text-muted-foreground">可直接修改时间码和字幕正文，AI 处理结果也会回填到这里。</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={addEntry}>新增行</Button>
                <Button variant="outline" size="sm" onClick={cleanupEntries} disabled={!entries.length}>清理空白</Button>
              </div>
            </div>

            {entries.length === 0 ? (
              <div className="grid min-h-[560px] place-items-center p-6">
                <div className="max-w-sm rounded-xl border border-dashed p-5 text-center">
                  <div className="text-sm font-medium">还没有可编辑字幕</div>
                  <p className="mt-2 text-xs text-muted-foreground">先读取字幕文件，或把 SRT / VTT 文本粘贴进来解析。</p>
                </div>
              </div>
            ) : (
              <div className="grid min-h-[560px] grid-cols-[120px_minmax(0,1fr)] max-xl:grid-cols-1">
                <div className="overflow-auto border-r p-2 max-xl:max-h-28 max-xl:border-r-0 max-xl:border-b">
                  <div className="grid grid-cols-1 gap-1 max-xl:grid-cols-[repeat(auto-fill,minmax(72px,1fr))]">
                    {entries.map((entry, index) => (
                      <button
                        key={`${entry.index}-${index}`}
                        onClick={() => setSelectedIndex(index)}
                        className={selectedIndex === index
                          ? 'rounded-md border border-primary bg-primary/10 px-2 py-2 text-left text-primary'
                          : timeToMs(entry.end) <= timeToMs(entry.start)
                            ? 'rounded-md border border-destructive/40 px-2 py-2 text-left text-destructive'
                            : 'rounded-md border bg-background px-2 py-2 text-left text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground'}
                      >
                        <div className="text-xs font-medium">#{index + 1}</div>
                        <div className="mt-1 truncate font-mono text-[10px] opacity-75">{entry.start}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="overflow-auto p-4">
                  {selectedEntry && (
                    <div className="mx-auto max-w-3xl space-y-4">
                      <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3 max-sm:grid-cols-1">
                        <TextField label="开始时间" value={selectedEntry.start} onChange={(value) => updateEntry(selectedIndex, 'start', value)} />
                        <TextField label="结束时间" value={selectedEntry.end} onChange={(value) => updateEntry(selectedIndex, 'end', value)} />
                        <Button variant="outline" className="max-sm:w-full" onClick={() => removeEntry(selectedIndex)}>
                          删除
                        </Button>
                      </div>

                      <label className="block">
                        <span className="mb-1 block text-xs text-muted-foreground">字幕正文</span>
                        <textarea
                          value={selectedEntry.text}
                          onChange={(event) => updateEntry(selectedIndex, 'text', event.target.value)}
                          rows={10}
                          className="w-full resize-y rounded-md border bg-background px-3 py-2 text-sm leading-6 outline-none transition-colors focus:border-primary"
                        />
                      </label>

                      <div className="rounded-xl border bg-background p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div>
                            <h4 className="text-sm font-medium">画面预览</h4>
                            <p className="mt-1 text-xs text-muted-foreground">用于快速确认断句、换行和可读性。</p>
                          </div>
                          <span className="rounded-md border px-2 py-1 font-mono text-[10px] text-muted-foreground">
                            {selectedEntry.start} - {selectedEntry.end}
                          </span>
                        </div>
                        <div className="rounded-lg bg-black px-4 py-6 text-center text-lg font-semibold leading-snug text-white [text-shadow:0_2px_4px_#000,1px_0_#000,-1px_0_#000,0_1px_#000,0_-1px_#000]">
                          {selectedEntry.text.split(/\r?\n/).map((line, index) => (
                            <div key={`${line}-${index}`}>{line || ' '}</div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>

          <div className="space-y-4">
            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="AI 单独处理" description="先选处理范围，再决定只做翻译、润色或生成，不绑定一键流程。" />
              <div className="mt-3 space-y-3">
                <SelectField
                  label="文本 API 配置"
                  value={selectedProfileId ? String(selectedProfileId) : ''}
                  options={textProfiles.map((profile) => [String(profile.id), profile.name] as FieldOption)}
                  placeholder={textProfiles.length ? '选择一个文本 API 配置' : '还没有文本 API 配置'}
                  onChange={handleSelectProfile}
                  description={selectedProfile ? `${selectedProfile.name} · ${selectedProfile.model || '未设置模型'}` : '先配置可用的文本 API，AI 字幕工具才可使用。'}
                />
                <SegmentedField label="处理范围" value={aiScope} options={AI_SCOPE_OPTIONS} onChange={(value) => setAiScope(value as 'all' | 'current')} />
                <SelectField label="翻译目标语言" value={targetLanguage} options={TARGET_LANG_OPTIONS} onChange={handleTargetLanguageChange} />
                <div className="grid gap-2">
                  <Button onClick={() => handleAiProcess('polish')} disabled={isAiProcessing || !entries.length || !selectedProfileId}>
                    <Wand2 className="mr-2 size-4" />
                    {isAiProcessing && activeAiLabel === AI_OPERATION_LABEL.polish ? 'AI 润色中…' : 'AI 润色'}
                  </Button>
                  <Button variant="outline" onClick={() => handleAiProcess('translate')} disabled={isAiProcessing || !entries.length || !selectedProfileId}>
                    <Languages className="mr-2 size-4" />
                    {isAiProcessing && activeAiLabel === AI_OPERATION_LABEL.translate ? 'AI 翻译中…' : 'AI 翻译'}
                  </Button>
                  <Button variant="outline" onClick={() => handleAiProcess('generate')} disabled={isAiProcessing || !entries.length || !selectedProfileId}>
                    <Sparkles className="mr-2 size-4" />
                    {isAiProcessing && activeAiLabel === AI_OPERATION_LABEL.generate ? 'AI 生成中…' : 'AI 生成文案'}
                  </Button>
                </div>
                {!textProfiles.length && (
                  <Button variant="secondary" className="w-full" onClick={onOpenTextSettings}>
                    <Settings2 className="mr-2 size-4" />
                    去配置文本 API
                  </Button>
                )}
              </div>
            </section>

            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="保存输出" description="校对完成后可单独保存 SRT，也可按当前字幕样式生成 ASS；如果绑定了任务，还能直接重新合成导出。" />
              <div className="mt-3 space-y-3">
                <TextField label="文件名" value={fileName} onChange={setFileName} />
                <TextField label="SRT 输出路径（可选）" value={outputPath} placeholder="留空自动保存到当前字幕或当前视频目录" onChange={setOutputPath} />
                <div className="grid grid-cols-2 gap-2">
                  <Button onClick={saveSrt} disabled={isSaving || !entries.length}>保存 SRT</Button>
                  <Button variant="outline" onClick={saveAss} disabled={isSaving || !entries.length}>生成 ASS</Button>
                </div>
                <Button
                  variant="secondary"
                  className="w-full"
                  onClick={reExport}
                  disabled={isReExporting || !entries.length || !selectedJob?.id || !selectedJobSourceVideo}
                >
                  {isReExporting ? '重新合并中…' : '重新合并导出'}
                </Button>
                {selectedJob && !selectedJobSourceVideo && (
                  <p className="text-xs text-warning">当前选中任务没有可复用源视频，暂时不能重新导出。</p>
                )}
                {lastSavedPath && (
                  <div className="rounded-lg border border-success/30 bg-success/10 p-3">
                    <div className="text-xs font-medium text-success">最近保存</div>
                    <p className="mt-1 break-all text-xs text-muted-foreground">{lastSavedPath}</p>
                  </div>
                )}
              </div>
            </section>

            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="校对统计" description="保存前快速看条目数量、字符数和异常时间。" />
              <div className="mt-4 grid grid-cols-2 gap-2">
                <MetricTile label="字幕条目" value={String(entries.length)} />
                <MetricTile label="正文字符" value={String(totalChars)} />
                <MetricTile label="异常时间" value={String(invalidCount)} tone={invalidCount ? 'warn' : 'default'} />
                <MetricTile label="当前序号" value={entries.length ? String(selectedIndex + 1) : '-'} />
              </div>
            </section>

            <section className="rounded-xl border bg-background/60 p-4">
              <SectionTitle title="纯文本检查" description="只看字幕正文，方便发现重复句、漏字和格式问题。" />
              <textarea
                value={plainText}
                readOnly
                rows={10}
                className="mt-3 w-full resize-none rounded-md border bg-background px-3 py-2 text-xs leading-5 text-muted-foreground outline-none"
              />
            </section>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function canSave(entries: SubtitleEntry[], invalidCount: number, setNotice: (notice: CorrectionNotice) => void) {
  if (!entries.length) {
    setNotice({ type: 'warning', message: '没有可保存的字幕条目。' })
    return false
  }
  if (invalidCount > 0) {
    setNotice({ type: 'warning', message: `有 ${invalidCount} 条字幕结束时间不晚于开始时间，请先修正。` })
    return false
  }
  return true
}

function normalizeEntries(entries: SubtitleEntry[]) {
  return entries.map((entry, index) => ({
    ...entry,
    index: index + 1,
    start: entry.start || '00:00:00,000',
    end: entry.end || entry.start || '00:00:00,000',
  }))
}

function timeToMs(value: string) {
  const parts = value.trim().replace(',', '.').split(':')
  if (parts.length !== 3) return 0
  const hours = Number(parts[0])
  const minutes = Number(parts[1])
  const seconds = Number(parts[2])
  if (![hours, minutes, seconds].every(Number.isFinite)) return 0
  return Math.max(0, Math.round((hours * 3600 + minutes * 60 + seconds) * 1000))
}

function msToTime(value: number) {
  const safeValue = Math.max(0, Math.round(value))
  const hours = Math.floor(safeValue / 3600000)
  const minutes = Math.floor((safeValue % 3600000) / 60000)
  const seconds = Math.floor((safeValue % 60000) / 1000)
  const millis = safeValue % 1000
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${String(millis).padStart(3, '0')}`
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function normalizeAssFileName(value: string) {
  const normalized = value.replace(/\.(srt|vtt|ass)$/i, '.ass')
  return /\.ass$/i.test(normalized) ? normalized : `${normalized}.ass`
}

function buildAssOutputPath(value: string) {
  if (!value) return undefined
  const normalized = value.replace(/\.(srt|vtt|ass)$/i, '.ass')
  return /\.ass$/i.test(normalized) ? normalized : `${normalized}.ass`
}

// 从现有输入里挑一个路径给后端推导工作目录，保证字幕保存仍落回当前视频文件夹。
function pickSubtitleWorkspaceSource(...candidates: Array<string | null | undefined>) {
  for (const candidate of candidates) {
    const value = (candidate || '').trim()
    if (value) return value
  }
  return undefined
}

function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

function NoticeBox({ notice }: { notice: CorrectionNotice }) {
  const classes = {
    info: 'border-accent/30 bg-accent/10 text-accent',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    error: 'border-destructive/30 bg-destructive/10 text-destructive',
  }[notice.type]
  const Icon = notice.type === 'error' || notice.type === 'warning' ? Bot : FileText

  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${classes}`} role={notice.type === 'error' ? 'alert' : 'status'}>
      <Icon className="mt-0.5 size-3.5 shrink-0" />
      <span>{notice.message}</span>
    </div>
  )
}

function MetricTile({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'warn' }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone === 'warn' ? 'text-warning' : 'text-foreground'}`}>{value}</div>
    </div>
  )
}

function PathRow({
  icon: Icon,
  label,
  path,
  emptyText,
}: {
  icon: typeof FileText
  label: string
  path: string
  emptyText: string
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-foreground">
        <Icon className="size-3.5" />
        <span>{label}</span>
      </div>
      <p className="mt-1 break-all">{path || emptyText}</p>
    </div>
  )
}
