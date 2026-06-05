// src/features/subtitle/StudioSubtitleWorkbench.tsx
// 工作台字幕调整区 - 支持读取字幕文件、逐条手动校对、AI 润色/翻译/生成，并保留时间轴

import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, ChevronLeft, ChevronRight, FileText, Film, Languages, Search, Settings2, Sparkles, Volume2, Wand2 } from 'lucide-react'
import { subtitleApi, profileApi, automationApi } from '@/lib/api'
import { useAutomationStore } from '@/stores/automationStore'
import { useTaskStore } from '@/stores/taskStore'
import { usePrefsStore } from '@/stores/prefsStore'
import type { ApiProfile, AutomationJob, SubtitleEntry, SubtitleTextOperation } from '@/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { SelectField, SegmentedField, type FieldOption } from '@/components/fields'

const SAMPLE_SUBTITLE_TEXT = `1
00:00:01,000 --> 00:00:03,000
这里可以粘贴 SRT 或 VTT 字幕文本。

2
00:00:03,200 --> 00:00:05,400
解析后就能逐条手动校对，也能单独做 AI 翻译或润色。`

const TARGET_LANG_OPTIONS: FieldOption[] = [['zh-CN', '中文 简体'], ['en', '英文'], ['ja', '日文'], ['ko', '韩文'], ['es', '西班牙语']]
const AI_SCOPE_OPTIONS: FieldOption[] = [['checked', '已勾选'], ['current', '当前条'], ['all', '全部']]
const SUBTITLE_LIST_ROW_HEIGHT = 68
const SUBTITLE_LIST_OVERSCAN = 8
const SUBTITLE_LIST_VISIBLE_COUNT = 18
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
  const [targetLanguage, setTargetLanguage] = useState('zh-CN')
  const [aiScope, setAiScope] = useState<'checked' | 'current' | 'all'>('checked')
  const [isAiProcessing, setIsAiProcessing] = useState(false)
  const [activeAiLabel, setActiveAiLabel] = useState('')
  const [isReExporting, setIsReExporting] = useState(false)
  const [entryKeyword, setEntryKeyword] = useState('')
  const [checkedEntryIndexes, setCheckedEntryIndexes] = useState<number[]>([])
  const [resolvedJobSubtitlePaths, setResolvedJobSubtitlePaths] = useState<Record<string, string>>({})
  const [showPasteImport, setShowPasteImport] = useState(false)
  const [listScrollTop, setListScrollTop] = useState(0)
  const autoLoadKeyRef = useRef('')
  const deferredEntryKeyword = useDeferredValue(entryKeyword.trim())

  const selectedEntry = entries[selectedIndex] || null
  const selectedEntryParts = useMemo(() => splitSubtitleByLanguage(selectedEntry?.text || ''), [selectedEntry?.text])
  const selectedOriginalText = selectedEntryParts.original
  const selectedTranslationText = selectedEntryParts.translation
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
  const selectedJobResolvedSubtitle = selectedJob?.id ? (resolvedJobSubtitlePaths[selectedJob.id] || '').trim() : ''
  const selectedJobSourceVideo = useMemo(() => resolveJobSourceVideo(selectedJob), [selectedJob])
  const selectedJobSubtitleCandidates = useMemo(
    () => buildJobSubtitleCandidates(selectedJob, selectedJobSourceVideo, selectedJobResolvedSubtitle),
    [selectedJob, selectedJobSourceVideo, selectedJobResolvedSubtitle],
  )
  const selectedJobSubtitleFile = useMemo(
    () => resolveExplicitSubtitlePath(selectedJob, selectedJobResolvedSubtitle),
    [selectedJob, selectedJobResolvedSubtitle],
  )
  const selectedJobVoicePath = useMemo(() => resolveJobVoicePath(selectedJob), [selectedJob])
  const jobOptions = useMemo<FieldOption[]>(
    () => [
      ['', '不绑定任务，仅手动处理'],
      ...availableJobs.map((job) => [job.id, `${JOB_STATUS_LABEL[job.status]} · ${job.title}`] as FieldOption),
    ],
    [availableJobs],
  )
  const selectedProfile = textProfiles.find((profile) => profile.id === selectedProfileId) || null
  const filteredEntryIndexes = useMemo(() => {
    if (!deferredEntryKeyword) {
      return entries.map((_, index) => index)
    }
    const keyword = deferredEntryKeyword.toLowerCase()
    return entries.reduce<number[]>((result, entry, index) => {
      const searchableText = [
        `#${index + 1}`,
        entry.start,
        entry.end,
        entry.text.replace(/\r?\n/g, ' '),
      ].join(' ').toLowerCase()
      if (searchableText.includes(keyword)) {
        result.push(index)
      }
      return result
    }, [])
  }, [entries, deferredEntryKeyword])
  const selectedVisiblePosition = filteredEntryIndexes.findIndex((index) => index === selectedIndex)
  const validCheckedEntryIndexes = useMemo(
    () => checkedEntryIndexes.filter((index) => index >= 0 && index < entries.length),
    [checkedEntryIndexes, entries.length],
  )
  const validCheckedEntryIndexSet = useMemo(() => new Set(validCheckedEntryIndexes), [validCheckedEntryIndexes])
  const allVisibleChecked = filteredEntryIndexes.length > 0
    && filteredEntryIndexes.every((index) => validCheckedEntryIndexSet.has(index))
  const maxVisibleListStart = Math.max(0, filteredEntryIndexes.length - SUBTITLE_LIST_VISIBLE_COUNT)
  const visibleListStart = Math.min(
    maxVisibleListStart,
    Math.max(0, Math.floor(listScrollTop / SUBTITLE_LIST_ROW_HEIGHT) - SUBTITLE_LIST_OVERSCAN),
  )
  const visibleListEnd = Math.min(
    filteredEntryIndexes.length,
    visibleListStart + SUBTITLE_LIST_VISIBLE_COUNT + SUBTITLE_LIST_OVERSCAN * 2,
  )
  const visibleEntryIndexes = filteredEntryIndexes.slice(visibleListStart, visibleListEnd)
  const listTopSpacer = visibleListStart * SUBTITLE_LIST_ROW_HEIGHT
  const listBottomSpacer = Math.max(0, (filteredEntryIndexes.length - visibleListEnd) * SUBTITLE_LIST_ROW_HEIGHT)

  useEffect(() => {
    if (!subtitlePath && suggestedSubtitleFile) {
      setSubtitlePath(suggestedSubtitleFile)
    }
  }, [subtitlePath, suggestedSubtitleFile])

  useEffect(() => {
    const autoLoadKey = `${selectedJob?.id || ''}|${selectedJobSubtitleCandidates.join('|')}`
    if (!selectedJob?.id || !selectedJobSubtitleCandidates.length || autoLoadKeyRef.current === autoLoadKey) {
      return
    }
    autoLoadKeyRef.current = autoLoadKey
    void loadSubtitleCandidates(selectedJobSubtitleCandidates, { jobId: selectedJob.id, silent: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedJob?.id, selectedJobSubtitleCandidates.join('|')])

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

  useEffect(() => {
    if (!entries.length) {
      if (selectedIndex !== 0) {
        setSelectedIndex(0)
      }
      return
    }
    if (selectedIndex >= entries.length) {
      setSelectedIndex(entries.length - 1)
      return
    }
    if (!filteredEntryIndexes.length) {
      return
    }
    if (!filteredEntryIndexes.includes(selectedIndex)) {
      setSelectedIndex(filteredEntryIndexes[0])
    }
  }, [entries.length, filteredEntryIndexes, selectedIndex])

  const applyParsedEntries = (nextEntries: SubtitleEntry[], message: string, path?: string) => {
    const normalized = normalizeEntries(nextEntries)
    setEntries(normalized)
    setSelectedIndex(0)
    setCheckedEntryIndexes([])
    setNotice({ type: 'success', message })
    if (path) {
      setSubtitlePath(path)
      setOutputPath(path.toLowerCase().endsWith('.srt') ? path : '')
      setFileName(path.split(/[\\/]/).pop() || 'manual_subtitle.srt')
    }
  }

  const loadSubtitleCandidates = async (rawCandidates: string[], options?: { jobId?: string; silent?: boolean }) => {
    const candidates = Array.from(new Set(rawCandidates.map((item) => item.trim()).filter(Boolean)))
    if (!candidates.length) {
      if (!options?.silent) {
        setNotice({ type: 'warning', message: '请先填写 SRT / VTT / ASS 字幕文件路径。' })
      }
      return false
    }

    setIsLoading(true)
    if (!options?.silent) {
      setNotice({ type: 'info', message: '正在读取字幕文件...' })
    }
    let lastErrorMessage = ''
    try {
      for (const path of candidates) {
        try {
          const result = await subtitleApi.parseFile(path)
          applyParsedEntries(result.entries, result.message, result.output_path)
          if (options?.jobId && result.output_path) {
            setResolvedJobSubtitlePaths((current) => (
              current[options.jobId!] === result.output_path
                ? current
                : { ...current, [options.jobId!]: result.output_path }
            ))
          }
          addLog('info', result.message)
          return true
        } catch (error) {
          lastErrorMessage = error instanceof Error ? error.message : '未知错误'
        }
      }

      if (!options?.silent) {
        const message = candidates.length > 1
          ? `读取字幕失败：已尝试 ${candidates.length} 个候选文件，最后错误：${lastErrorMessage || '未知错误'}`
          : `读取字幕失败: ${lastErrorMessage || '未知错误'}`
        setNotice({ type: 'error', message })
        addLog('error', message)
      }
      return false
    } finally {
      setIsLoading(false)
    }
  }

  const handleParseFile = async (pathOverride?: string | string[]) => {
    const candidates = Array.isArray(pathOverride)
      ? pathOverride
      : [(pathOverride ?? subtitlePath).trim()]
    if (!candidates.some((item) => item.trim())) {
      setNotice({ type: 'warning', message: '请先填写 SRT / VTT / ASS 字幕文件路径。' })
      return
    }
    await loadSubtitleCandidates(candidates, { jobId: selectedJob?.id, silent: false })
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
    setEntries((current) => current.map((entry, itemIndex) => {
      if (itemIndex !== index) return entry
      const nextEntry = { ...entry, [key]: value }
      return normalizeEntry(nextEntry, itemIndex, true)
    }))
  }

  const updateSelectedOriginalText = (value: string) => {
    if (!selectedEntry) return
    updateEntry(selectedIndex, 'text', joinSubtitleLines(selectedTranslationText, value))
  }

  const updateSelectedTranslationText = (value: string) => {
    if (!selectedEntry) return
    updateEntry(selectedIndex, 'text', joinSubtitleLines(value, selectedOriginalText))
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
    setCheckedEntryIndexes((current) => [...current, nextEntries.length - 1])
  }

  const removeEntry = (index: number) => {
    const nextEntries = normalizeEntries(entries.filter((_, itemIndex) => itemIndex !== index))
    setEntries(nextEntries)
    setSelectedIndex(Math.max(0, Math.min(index, nextEntries.length - 1)))
    setCheckedEntryIndexes((current) => (
      current
        .filter((itemIndex) => itemIndex !== index)
        .map((itemIndex) => (itemIndex > index ? itemIndex - 1 : itemIndex))
    ))
  }

  const cleanupEntries = () => {
    const nextEntries = normalizeEntries(entries
      .map((entry) => {
        const { original, translation } = splitSubtitleByLanguage(entry.text)
        return { ...entry, text: joinSubtitleLines(translation, original) }
      })
      .filter((entry) => entry.text.trim()))
    setEntries(nextEntries)
    setSelectedIndex(Math.min(selectedIndex, Math.max(0, nextEntries.length - 1)))
    setCheckedEntryIndexes((current) => current.filter((index) => index < nextEntries.length))
    setNotice({ type: 'success', message: `已清理字幕，保留 ${nextEntries.length} 条。` })
  }

  const moveSelection = (offset: number) => {
    if (!filteredEntryIndexes.length) return
    const currentPosition = selectedVisiblePosition >= 0 ? selectedVisiblePosition : 0
    const nextPosition = Math.max(0, Math.min(filteredEntryIndexes.length - 1, currentPosition + offset))
    setSelectedIndex(filteredEntryIndexes[nextPosition])
  }

  const toggleEntryChecked = (index: number, checked?: boolean) => {
    setCheckedEntryIndexes((current) => {
      const exists = current.includes(index)
      const shouldCheck = checked ?? !exists
      if (shouldCheck && !exists) {
        return [...current, index].sort((left, right) => left - right)
      }
      if (!shouldCheck && exists) {
        return current.filter((itemIndex) => itemIndex !== index)
      }
      return current
    })
  }

  const toggleVisibleChecked = () => {
    setCheckedEntryIndexes((current) => {
      const visible = new Set(filteredEntryIndexes)
      if (allVisibleChecked) {
        return current.filter((index) => !visible.has(index))
      }
      return Array.from(new Set([...current, ...filteredEntryIndexes])).sort((left, right) => left - right)
    })
  }

  const resolveAiTargetIndexes = () => {
    if (aiScope === 'checked') {
      return validCheckedEntryIndexes
    }
    if (aiScope === 'current') {
      return selectedEntry ? [selectedIndex] : []
    }
    return entries.map((_, index) => index)
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
    const targetIndexes = resolveAiTargetIndexes()
    if (!targetIndexes.length) {
      setNotice({ type: 'warning', message: aiScope === 'checked' ? '请先在左侧勾选要处理的字幕。' : '当前没有可处理的字幕条目。' })
      return
    }
    const targetEntries = targetIndexes.map((index) => {
      const currentEntry = entries[index]
      if (operation !== 'translate') {
        return currentEntry
      }
      return {
        ...currentEntry,
        text: extractTranslationSourceText(currentEntry.text),
      }
    })
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
          const sourceText = targetEntries[itemIndex]?.text || ''
          const nextText = operation === 'translate'
            ? combineComparedSubtitleText(sourceText, entry.text)
            : entry.text
          next[targetIndex] = { ...next[targetIndex], start: entry.start, end: entry.end, text: nextText }
        })
        return normalizeEntries(next)
      })
      const successMessage = operation === 'translate'
        ? `${actionLabel}完成，已生成原文对照字幕 ${result.entries.length} 条。`
        : `${actionLabel}完成，已更新 ${result.entries.length} 条字幕。`
      setNotice({ type: 'success', message: successMessage })
      addLog('info', `${actionLabel}完成，范围：${aiScope === 'checked' ? '已勾选字幕' : aiScope === 'all' ? '全部字幕' : '当前条'}`)
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
      if (result.output_path) {
        setLastSavedPath(result.output_path)
      }
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
    <Card className="glass h-full min-h-0 py-0">
      <CardContent className="flex h-full min-h-0 flex-col gap-3 p-3">
        {notice && <NoticeBox notice={notice} />}

        <section className="shrink-0 rounded-xl border bg-background/60 p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionTitle title="路径与任务选择" description="读取后进入下方校对工作区。" />
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">{entries.length ? `${entries.length} 条字幕` : '未载入字幕'}</Badge>
              <Badge variant={validCheckedEntryIndexes.length ? 'default' : 'outline'}>已勾选 {validCheckedEntryIndexes.length}</Badge>
              {invalidCount > 0 && <Badge variant="destructive">异常时间 {invalidCount}</Badge>}
              <Button variant="outline" size="sm" onClick={() => setShowPasteImport((value) => !value)}>
                {showPasteImport ? '收起粘贴导入' : '粘贴导入'}
              </Button>
            </div>
          </div>

          <div className="mt-2 grid items-center gap-2 xl:grid-cols-[minmax(220px,300px)_minmax(0,1fr)_auto]">
            <CompactSelect value={selectedJob?.id || ''} options={jobOptions} onChange={handleSelectJob} placeholder={availableJobs.length ? '选择任务' : '暂无可选任务'} />
            <Input value={subtitlePath} placeholder="字幕文件路径：D:\\项目\\output\\subtitle.ass" onChange={(event) => setSubtitlePath(event.target.value)} />
            <div className="flex items-end gap-2">
              <Button className="h-10" onClick={() => handleParseFile()} disabled={isLoading}>
                {isLoading ? '读取中…' : '读取路径'}
              </Button>
              <Button
                variant="outline"
                className="h-10"
                onClick={() => handleParseFile(selectedJobSubtitleCandidates)}
                disabled={!selectedJobSubtitleCandidates.length || isLoading}
              >
                载入任务
              </Button>
            </div>
          </div>

          <div className="mt-2 grid items-center gap-2 xl:grid-cols-[minmax(180px,240px)_minmax(0,1fr)_auto]">
            <Input value={fileName} placeholder="文件名" onChange={(event) => setFileName(event.target.value)} />
            <Input value={outputPath} placeholder="SRT 输出路径（可选，留空自动保存到当前视频目录）" onChange={(event) => setOutputPath(event.target.value)} />
            <div className="flex items-end gap-2">
              <Button className="h-10" onClick={saveSrt} disabled={isSaving || !entries.length}>保存 SRT</Button>
              <Button variant="outline" className="h-10" onClick={saveAss} disabled={isSaving || !entries.length}>生成 ASS</Button>
              <Button
                variant="secondary"
                className="h-10"
                onClick={reExport}
                disabled={isReExporting || !entries.length || !selectedJob?.id || !selectedJobSourceVideo}
              >
                {isReExporting ? '合并中…' : '重新合并'}
              </Button>
            </div>
          </div>

          {selectedJob && (
            <div className="mt-2 grid gap-1 rounded-lg border border-primary/20 bg-primary/5 p-1.5 text-[11px] text-muted-foreground lg:grid-cols-3">
              <PathRow icon={FileText} label="字幕" path={selectedJobSubtitleFile} emptyText="未找到可编辑字幕" />
              <PathRow icon={Film} label="源视频" path={selectedJobSourceVideo} emptyText="未找到源视频" />
              <PathRow icon={Volume2} label="配音" path={selectedJobVoicePath} emptyText="无配音音轨" />
            </div>
          )}

          {lastSavedPath && (
            <div className="mt-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-xs">
              <span className="font-medium text-success">最近完成：</span>
              <span className="ml-2 break-all text-muted-foreground">{lastSavedPath}</span>
            </div>
          )}

          {showPasteImport && (
            <div className="mt-2 rounded-xl border bg-background p-2.5">
              <div className="flex items-center justify-between gap-3">
                <SectionTitle title="粘贴导入" description="辅助入口：粘贴文本后解析。" />
                <select
                  value={pasteFormat}
                  onChange={(event) => setPasteFormat(event.target.value as 'srt' | 'vtt')}
                  className="h-8 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-primary"
                >
                  <option value="srt">SRT</option>
                  <option value="vtt">VTT</option>
                </select>
              </div>
              <textarea
                value={pasteText}
                onChange={(event) => setPasteText(event.target.value)}
                rows={3}
                className="mt-2 w-full resize-none rounded-md border bg-background px-3 py-2 text-xs leading-5 outline-none transition-colors focus:border-primary"
              />
              <Button variant="outline" size="sm" className="mt-2 w-full" onClick={handleParseText} disabled={isLoading}>
                解析粘贴文本
              </Button>
            </div>
          )}
        </section>

        <div className="grid min-h-0 flex-1 gap-3 grid-cols-[250px_minmax(0,1fr)_300px] max-lg:grid-cols-1">
          <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-background/60">
            <div className="shrink-0 border-b p-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium">字幕列表</h3>
                  <p className="mt-1 text-xs text-muted-foreground">可单条选择，也可勾选后批量 AI 处理。</p>
                </div>
                <Button variant="outline" size="sm" onClick={toggleVisibleChecked} disabled={!filteredEntryIndexes.length}>
                  {allVisibleChecked ? '取消全选' : '全选'}
                </Button>
              </div>
              <div className="relative mt-3">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={entryKeyword}
                  onChange={(event) => setEntryKeyword(event.target.value)}
                  placeholder="搜索字幕/时间"
                  className="h-9 pl-8"
                />
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                <span>显示 {filteredEntryIndexes.length} / {entries.length}</span>
                <span>已勾选 {validCheckedEntryIndexes.length}</span>
              </div>
            </div>

            {entries.length === 0 ? (
              <div className="grid min-h-0 flex-1 place-items-center p-4 text-center text-xs text-muted-foreground">
                先在顶部选择路径或任务并读取字幕。
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto p-3" onScroll={(event) => setListScrollTop(event.currentTarget.scrollTop)}>
                {filteredEntryIndexes.length ? (
                  <>
                    <div style={{ height: listTopSpacer }} />
                    <div className="space-y-2">
                      {visibleEntryIndexes.map((index) => {
                  const entry = entries[index]
                  const lines = splitSubtitleByLanguage(entry.text)
                  const isInvalid = timeToMs(entry.end) <= timeToMs(entry.start)
                  const checked = validCheckedEntryIndexSet.has(index)
                  return (
                    <div
                      key={`${entry.index}-${index}`}
                      className={selectedIndex === index
                        ? 'rounded-xl border border-primary bg-primary/10 p-2 text-primary'
                        : isInvalid
                          ? 'rounded-xl border border-destructive/40 bg-background p-2 text-destructive'
                          : 'rounded-xl border bg-background p-2 text-foreground transition-colors hover:border-primary/40'}
                    >
                      <div className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) => toggleEntryChecked(index, event.target.checked)}
                          className="mt-1 size-4 rounded border-border"
                          aria-label={`勾选第 ${index + 1} 条字幕`}
                        />
                        <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setSelectedIndex(index)}>
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-medium">#{index + 1}</span>
                            <span className="font-mono text-[10px] opacity-75">{entry.start}</span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-sm leading-5">{lines.translation || lines.original || '空字幕'}</p>
                          {lines.original && <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{lines.original}</p>}
                        </button>
                      </div>
                    </div>
                  )
                      })}
                    </div>
                    <div style={{ height: listBottomSpacer }} />
                  </>
                ) : (
                  <div className="grid min-h-[240px] place-items-center rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">
                    没有匹配到字幕条目。
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="grid min-h-0 grid-rows-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3">
            <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-background/60 p-3">
              <div className="shrink-0">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-medium">原文</h3>
                    <p className="mt-1 text-xs text-muted-foreground">编辑原视频识别文本。</p>
                  </div>
                  {selectedEntry && (
                    <Badge variant="outline">
                      第 {selectedIndex + 1} 条 · {selectedEntry.start} - {selectedEntry.end}
                    </Badge>
                  )}
                </div>
              </div>
              <textarea
                value={selectedOriginalText}
                onChange={(event) => updateSelectedOriginalText(event.target.value)}
                disabled={!selectedEntry}
                className="mt-3 min-h-0 flex-1 resize-none rounded-lg border bg-background px-4 py-3 text-base leading-7 outline-none transition-colors focus:border-primary disabled:opacity-50"
                placeholder="选择左侧字幕后编辑原文"
              />
            </div>

            <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-background/60 p-3">
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium">译文</h3>
                  <p className="mt-1 text-xs text-muted-foreground">保存时按“译文 + 原文”输出双语字幕。</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => moveSelection(-1)} disabled={selectedVisiblePosition <= 0}>
                    <ChevronLeft className="mr-1 size-4" />
                    上一条
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => moveSelection(1)} disabled={selectedVisiblePosition < 0 || selectedVisiblePosition >= filteredEntryIndexes.length - 1}>
                    下一条
                    <ChevronRight className="ml-1 size-4" />
                  </Button>
                </div>
              </div>
              <textarea
                value={selectedTranslationText}
                onChange={(event) => updateSelectedTranslationText(event.target.value)}
                disabled={!selectedEntry}
                className="mt-3 min-h-0 flex-1 resize-none rounded-lg border bg-background px-4 py-3 text-base leading-7 outline-none transition-colors focus:border-primary disabled:opacity-50"
                placeholder="这里编辑翻译后的字幕"
              />
            </div>
          </section>

          <aside className="rounded-xl border bg-background/60 p-3">
            <div className="mb-2 shrink-0">
              <h3 className="text-sm font-medium">操作栏</h3>
              <p className="mt-1 text-xs text-muted-foreground">AI 单独处理。</p>
            </div>

            <div className="space-y-2">
              <section className="space-y-2 rounded-xl border bg-background p-2.5">
                <SelectField
                  label="文本 API 配置"
                  value={selectedProfileId ? String(selectedProfileId) : ''}
                  options={textProfiles.map((profile) => [String(profile.id), profile.name] as FieldOption)}
                  placeholder={textProfiles.length ? '选择文本 API 配置' : '还没有文本 API 配置'}
                  onChange={handleSelectProfile}
                  description={selectedProfile ? `${selectedProfile.name} · ${selectedProfile.model || '未设置模型'}` : undefined}
                />
                <SegmentedField label="处理范围" value={aiScope} options={AI_SCOPE_OPTIONS} onChange={(value) => setAiScope(value as 'checked' | 'current' | 'all')} />
                <SelectField label="翻译目标语言" value={targetLanguage} options={TARGET_LANG_OPTIONS} onChange={handleTargetLanguageChange} />
                <div className="grid gap-1.5">
                  <Button onClick={() => handleAiProcess('translate')} disabled={isAiProcessing || !entries.length || !selectedProfileId}>
                    <Languages className="mr-2 size-4" />
                    {isAiProcessing && activeAiLabel === AI_OPERATION_LABEL.translate ? 'AI 翻译中…' : 'AI 翻译'}
                  </Button>
                  <Button variant="outline" onClick={() => handleAiProcess('polish')} disabled={isAiProcessing || !entries.length || !selectedProfileId}>
                    <Wand2 className="mr-2 size-4" />
                    {isAiProcessing && activeAiLabel === AI_OPERATION_LABEL.polish ? 'AI 润色中…' : 'AI 润色'}
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
              </section>
            </div>
          </aside>
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
  return entries
    .map((entry, index) => normalizeEntry(entry, index, true))
    .filter((entry) => !isMeaninglessSubtitleText(entry.text))
    .map((entry, index) => ({
      ...entry,
      index: index + 1,
      start: entry.start || '00:00:00,000',
      end: entry.end || entry.start || '00:00:00,000',
    }))
}

function normalizeEntry(entry: SubtitleEntry, index: number, cleanText: boolean) {
  const text = cleanText ? cleanSubtitleTextForDisplay(entry.text) : String(entry.text || '')
  return {
    ...entry,
    index: index + 1,
    text,
    start: entry.start || '00:00:00,000',
    end: entry.end || entry.start || '00:00:00,000',
  }
}

function splitSubtitleLines(text: string) {
  const lines = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\\N/g, '\n')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return {
    all: lines,
    primary: lines[0] || '',
    secondary: lines.slice(1).join('\n'),
  }
}

function splitSubtitleByLanguage(text: string) {
  const lines = splitSubtitleLines(text).all
  const originalLines: string[] = []
  const translationLines: string[] = []

  for (const line of lines) {
    if (isChineseText(line)) {
      translationLines.push(line)
    } else {
      originalLines.push(line)
    }
  }

  if (!originalLines.length && !translationLines.length) {
    return { original: '', translation: '' }
  }

  if (!translationLines.length && originalLines.length === 1 && isLikelyTranslatedChinese(originalLines[0])) {
    return { original: '', translation: originalLines[0] }
  }

  return {
    original: originalLines.join('\n'),
    translation: translationLines.join('\n'),
  }
}

function joinSubtitleLines(primary: string, secondary: string) {
  const normalizedPrimary = primary.trim()
  const normalizedSecondary = secondary
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .join('\n')
  return [normalizedPrimary, normalizedSecondary].filter(Boolean).join('\n')
}

function extractTranslationSourceText(text: string) {
  const { original, translation } = splitSubtitleByLanguage(text)
  return original || translation
}

function combineComparedSubtitleText(originalText: string, translatedText: string) {
  const normalizedOriginal = originalText.trim()
  const normalizedTranslated = translatedText.trim()
  if (!normalizedOriginal) return normalizedTranslated
  if (!normalizedTranslated || normalizedOriginal === normalizedTranslated) return normalizedOriginal
  return `${normalizedTranslated}\n${normalizedOriginal}`
}

function isChineseText(value: string) {
  return /[\u3400-\u9fff]/.test(value)
}

function isLikelyTranslatedChinese(value: string) {
  return isChineseText(value)
}

function isMeaninglessSubtitleText(text: string) {
  return /^[\s，。、！？；：,.!?;:…]+$/.test(String(text || '').trim())
}

function cleanSubtitleTextForDisplay(text: string) {
  return String(text || '')
    .replace(/\.{3,}|…+|[，。,.]/g, '')
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
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

function resolveExplicitSubtitlePath(job: AutomationJob | null, resolvedPath = '') {
  const directPath = (job?.subtitle_asset_path || '').trim()
  if (isEditableSubtitlePath(directPath)) return directPath
  if (isEditableSubtitlePath(resolvedPath)) return resolvedPath
  const stagePath = (job?.steps.find((step) => step.key === 'subtitle')?.output_path || '').trim()
  return isEditableSubtitlePath(stagePath) ? stagePath : ''
}

function resolveJobSourceVideo(job: AutomationJob | null) {
  const directPath = (job?.source_video_path || '').trim()
  if (isMediaPath(directPath)) return directPath
  for (const key of ['effects', 'download'] as const) {
    const stagePath = (job?.steps.find((step) => step.key === key)?.output_path || '').trim()
    if (isMediaPath(stagePath)) return stagePath
  }
  return ''
}

function resolveJobVoicePath(job: AutomationJob | null) {
  const directPath = (job?.voice_asset_path || '').trim()
  if (isMediaPath(directPath)) return directPath
  const stagePath = (job?.steps.find((step) => step.key === 'voice')?.output_path || '').trim()
  return isMediaPath(stagePath) ? stagePath : ''
}

function buildJobSubtitleCandidates(job: AutomationJob | null, sourceVideoPath: string, resolvedPath = '') {
  const candidates = new Set<string>()
  const directPath = resolveExplicitSubtitlePath(job, resolvedPath)
  if (directPath) {
    candidates.add(directPath)
  }

  const subtitleStagePath = (job?.steps.find((step) => step.key === 'subtitle')?.output_path || '').trim()
  if (isEditableSubtitlePath(subtitleStagePath)) {
    candidates.add(subtitleStagePath)
  }

  const legacyCandidates = buildLegacySubtitleCandidates(subtitleStagePath, sourceVideoPath)
  for (const candidate of legacyCandidates) {
    candidates.add(candidate)
  }

  return Array.from(candidates)
}

function buildLegacySubtitleCandidates(subtitleStagePath: string, sourceVideoPath: string) {
  const candidates: string[] = []
  const baseNames = new Set<string>()
  const outputDirectories = new Set<string>()

  if (sourceVideoPath) {
    baseNames.add(stripStageSuffix(sourceVideoPath))
    const sourceOutputDir = siblingOutputDirectory(sourceVideoPath)
    if (sourceOutputDir) outputDirectories.add(sourceOutputDir)
  }

  if (subtitleStagePath) {
    baseNames.add(stripStageSuffix(subtitleStagePath))
    const subtitleOutputDir = outputDirectoryFromPath(subtitleStagePath)
    if (subtitleOutputDir) outputDirectories.add(subtitleOutputDir)
  }

  for (const outputDirectory of outputDirectories) {
    for (const baseName of baseNames) {
      if (!baseName) continue
      candidates.push(
        `${outputDirectory}\\${baseName}.ass`,
        `${outputDirectory}\\${baseName}.srt`,
        `${outputDirectory}\\${baseName}.vtt`,
      )
      for (const language of ['zh', 'zh-CN', 'zh_Hans', 'en', 'ja', 'ko']) {
        candidates.push(
          `${outputDirectory}\\${baseName}_${language}.ass`,
          `${outputDirectory}\\${baseName}_${language}.srt`,
          `${outputDirectory}\\${baseName}_${language}.vtt`,
        )
      }
    }
  }

  return candidates.filter(Boolean)
}

function stripStageSuffix(path: string) {
  const baseName = path.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || ''
  return baseName.replace(/_(subtitled|voiced|manual_final|final|enhanced|preview)$/i, '').trim()
}

function outputDirectoryFromPath(path: string) {
  const normalized = path.trim()
  if (!normalized) return ''
  const parent = normalized.replace(/[\\/][^\\/]+$/, '')
  return /[\\/]output$/i.test(parent) ? parent : ''
}

function siblingOutputDirectory(path: string) {
  const normalized = path.trim()
  if (!normalized) return ''
  const parent = normalized.replace(/[\\/][^\\/]+$/, '')
  if (/[\\/]downloads$/i.test(parent) || /[\\/]exports$/i.test(parent)) {
    return parent.replace(/[\\/]([^\\/]+)$/i, '\\output')
  }
  return /[\\/]output$/i.test(parent) ? parent : ''
}

function isEditableSubtitlePath(path: string | null | undefined) {
  return Boolean(path && /\.(srt|vtt|ass)$/i.test(path))
}

function isMediaPath(path: string | null | undefined) {
  return Boolean(path && /\.(mp4|mov|mkv|webm|avi|m4v|mp3|wav|m4a|aac|flac)$/i.test(path))
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

function CompactSelect({
  value,
  options,
  onChange,
  placeholder,
}: {
  value: string
  options: FieldOption[]
  onChange: (value: string) => void
  placeholder: string
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary"
    >
      {!options.length && <option value="">{placeholder}</option>}
      {options.map(([optionValue, optionLabel]) => (
        <option key={optionValue || 'empty'} value={optionValue}>{optionLabel}</option>
      ))}
    </select>
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
    <div className="min-w-0 rounded-md bg-background/40 px-2 py-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-foreground" />
        <span className="shrink-0 text-foreground">{label}</span>
        <span className="truncate" title={path || emptyText}>{path || emptyText}</span>
      </div>
    </div>
  )
}
