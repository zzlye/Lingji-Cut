// src/features/subtitle/SubtitleCorrectionPanel.tsx
// 字幕校对面板 - 导入、粘贴、逐条修正并保存 SRT/ASS 字幕

import { useMemo, useState } from 'react'
import { subtitleApi } from '@/lib/api'
import { loadAutomationPreferences } from '@/lib/automationPreferences'
import { useTaskStore } from '@/stores/taskStore'
import type { SubtitleEntry } from '@/types'

/** 字幕文本示例，方便空状态快速试用校对流程 */
const SAMPLE_SUBTITLE_TEXT = `1
00:00:01,000 --> 00:00:03,000
这里可以粘贴 YouTube 原字幕或 AI 处理后的字幕。

2
00:00:03,200 --> 00:00:05,400
然后在下方逐条修正错字、时间和断句。`

/** 面板内操作反馈 */
type CorrectionNotice = {
  type: 'info' | 'success' | 'warning' | 'error'
  message: string
} | null

/**
 * 字幕校对面板
 * 布局采用工作台形态：左侧导入，中间编辑，右侧导出和统计。
 */
export function SubtitleCorrectionPanel() {
  const [subtitlePath, setSubtitlePath] = useState('')
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
  const { addLog } = useTaskStore()

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

  /** 用后端返回结果刷新编辑区 */
  const applyParsedEntries = (nextEntries: SubtitleEntry[], message: string, path?: string) => {
    const normalized = normalizeEntries(nextEntries)
    setEntries(normalized)
    setSelectedIndex(0)
    setNotice({ type: 'success', message })
    if (path) {
      setSubtitlePath(path)
      setOutputPath(path.toLowerCase().endsWith('.srt') ? path : '')
    }
  }

  /** 读取本地字幕文件 */
  const handleParseFile = async () => {
    if (!subtitlePath.trim()) {
      setNotice({ type: 'warning', message: '请先填写 SRT 或 VTT 字幕文件路径。' })
      return
    }

    setIsLoading(true)
    setNotice({ type: 'info', message: '正在读取字幕文件...' })
    try {
      const result = await subtitleApi.parseFile(subtitlePath)
      applyParsedEntries(result.entries, result.message, result.output_path)
      addLog('info', result.message)
    } catch (error) {
      const message = `读取字幕失败: ${error instanceof Error ? error.message : '未知错误'}`
      setNotice({ type: 'error', message })
      addLog('error', message)
    } finally {
      setIsLoading(false)
    }
  }

  /** 解析粘贴文本 */
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

  /** 更新单条字幕字段 */
  const updateEntry = <K extends keyof SubtitleEntry>(index: number, key: K, value: SubtitleEntry[K]) => {
    setEntries((current) => normalizeEntries(current.map((entry, itemIndex) => (
      itemIndex === index ? { ...entry, [key]: value } : entry
    ))))
  }

  /** 添加新字幕条目 */
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

  /** 删除当前字幕条目 */
  const removeEntry = (index: number) => {
    const nextEntries = normalizeEntries(entries.filter((_, itemIndex) => itemIndex !== index))
    setEntries(nextEntries)
    setSelectedIndex(Math.max(0, Math.min(index, nextEntries.length - 1)))
  }

  /** 清理字幕空白和空条目 */
  const cleanupEntries = () => {
    const nextEntries = normalizeEntries(entries
      .map((entry) => ({ ...entry, text: entry.text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).join('\n') }))
      .filter((entry) => entry.text.trim()))
    setEntries(nextEntries)
    setSelectedIndex(Math.min(selectedIndex, Math.max(0, nextEntries.length - 1)))
    setNotice({ type: 'success', message: `已清理字幕，保留 ${nextEntries.length} 条。` })
  }

  /** 按关键词快速定位字幕 */
  const findByKeyword = () => {
    const keyword = window.prompt('输入要查找的字幕内容')
    if (!keyword) return
    const index = entries.findIndex((entry) => entry.text.includes(keyword))
    if (index >= 0) {
      setSelectedIndex(index)
      setNotice({ type: 'info', message: `已定位到第 ${index + 1} 条字幕。` })
    } else {
      setNotice({ type: 'warning', message: '没有找到匹配字幕。' })
    }
  }

  /** 保存 SRT 字幕 */
  const saveSrt = async () => {
    if (!canSave(entries, invalidCount, setNotice)) return

    setIsSaving(true)
    setNotice({ type: 'info', message: '正在保存 SRT 字幕...' })
    try {
      const result = await subtitleApi.saveCorrected({
        entries,
        output_path: outputPath.trim() || undefined,
        file_name: fileName.trim() || undefined,
        format: 'srt',
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

  /** 生成 ASS 字幕 */
  const saveAss = async () => {
    if (!canSave(entries, invalidCount, setNotice)) return

    setIsSaving(true)
    setNotice({ type: 'info', message: '正在生成 ASS 字幕...' })
    try {
      const preferences = loadAutomationPreferences()
      const result = await subtitleApi.saveAss({
        entries,
        file_name: fileName.replace(/\.(srt|vtt)$/i, '.ass'),
        preset_id: preferences.subtitle_preset_id,
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-medium">字幕校对</h3>
        <p className="mt-1 text-xs text-foreground-muted">导入字幕后逐条修正错字、断句和时间轴，再保存为 SRT 或按当前字幕样式生成 ASS。</p>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid min-h-full grid-cols-[minmax(260px,320px)_minmax(420px,1fr)_minmax(240px,300px)] gap-4 max-2xl:grid-cols-[minmax(260px,320px)_minmax(0,1fr)] max-lg:grid-cols-1">
          <aside className="space-y-4">
            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="导入字幕" description="支持 SRT/VTT 文件路径，也可以直接粘贴字幕文本。" />
              <TextField label="字幕文件路径" value={subtitlePath} placeholder="D:\视频项目\output\subtitle.srt" onChange={setSubtitlePath} />
              <button onClick={handleParseFile} disabled={isLoading} className="mt-3 h-9 w-full rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {isLoading ? '读取中...' : '读取文件'}
              </button>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <div className="flex items-start justify-between gap-2">
                <SectionTitle title="粘贴解析" description="适合从任务结果、AI 返回或剪贴板快速进入校对。" />
                <select
                  value={pasteFormat}
                  onChange={(event) => setPasteFormat(event.target.value as 'srt' | 'vtt')}
                  className="h-8 rounded-md border border-border bg-background-elevated px-2 text-xs outline-none focus:border-primary"
                >
                  <option value="srt">SRT</option>
                  <option value="vtt">VTT</option>
                </select>
              </div>
              <textarea
                value={pasteText}
                onChange={(event) => setPasteText(event.target.value)}
                rows={9}
                className="mt-3 w-full resize-none rounded-md border border-border bg-background-elevated px-3 py-2 font-mono text-xs outline-none focus:border-primary"
              />
              <button onClick={handleParseText} disabled={isLoading} className="mt-3 h-9 w-full rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50">
                解析文本
              </button>
            </section>

            <NoticeBox notice={notice} />
          </aside>

          <main className="flex min-h-[560px] min-w-0 flex-col rounded-lg border border-border bg-background">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <h4 className="text-sm font-medium">逐条修正</h4>
                <p className="mt-1 text-xs text-foreground-muted">点击左侧序号定位，直接修改时间码和字幕正文。</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button onClick={addEntry} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5">
                  新增行
                </button>
                <button onClick={cleanupEntries} disabled={!entries.length} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5 disabled:opacity-50">
                  清理空白
                </button>
                <button onClick={findByKeyword} disabled={!entries.length} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5 disabled:opacity-50">
                  查找
                </button>
              </div>
            </div>

            {entries.length === 0 ? (
              <div className="flex min-h-0 flex-1 items-center justify-center p-6">
                <div className="max-w-sm rounded-lg border border-dashed border-border p-5 text-center">
                  <div className="text-sm font-medium">还没有字幕条目</div>
                  <p className="mt-2 text-xs text-foreground-muted">先从左侧读取字幕文件，或粘贴 SRT/VTT 文本并解析。</p>
                </div>
              </div>
            ) : (
              <div className="grid min-h-0 flex-1 grid-cols-[120px_minmax(0,1fr)] max-xl:grid-cols-1">
                <div className="min-h-0 overflow-auto border-r border-border p-2 max-xl:max-h-28 max-xl:border-r-0 max-xl:border-b">
                  <div className="grid grid-cols-1 gap-1 max-xl:grid-cols-[repeat(auto-fill,minmax(72px,1fr))]">
                    {entries.map((entry, index) => (
                      <button
                        key={`${entry.index}-${index}`}
                        onClick={() => setSelectedIndex(index)}
                        className={`rounded-md border px-2 py-2 text-left transition-colors ${
                          selectedIndex === index
                            ? 'border-primary bg-primary/10 text-primary'
                            : timeToMs(entry.end) <= timeToMs(entry.start)
                              ? 'border-destructive/40 text-destructive'
                              : 'border-border bg-background-elevated text-foreground-muted hover:border-border-bright hover:text-foreground'
                        }`}
                      >
                        <div className="text-xs font-medium">#{index + 1}</div>
                        <div className="mt-1 truncate font-mono text-[10px] opacity-75">{entry.start}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="min-h-0 overflow-auto p-4">
                  {selectedEntry && (
                    <div className="mx-auto max-w-3xl space-y-4">
                      <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3 max-sm:grid-cols-1">
                        <TextField label="开始时间" value={selectedEntry.start} onChange={(value) => updateEntry(selectedIndex, 'start', value)} />
                        <TextField label="结束时间" value={selectedEntry.end} onChange={(value) => updateEntry(selectedIndex, 'end', value)} />
                        <button onClick={() => removeEntry(selectedIndex)} className="h-9 rounded-md border border-border px-4 text-sm text-destructive hover:bg-white/5">
                          删除
                        </button>
                      </div>
                      <label className="block">
                        <span className="mb-1 block text-xs text-foreground-muted">字幕正文</span>
                        <textarea
                          value={selectedEntry.text}
                          onChange={(event) => updateEntry(selectedIndex, 'text', event.target.value)}
                          rows={9}
                          className="w-full resize-y rounded-md border border-border bg-background-elevated px-3 py-2 text-sm leading-6 outline-none focus:border-primary"
                        />
                      </label>
                      <div className="rounded-lg border border-border bg-background-elevated p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div>
                            <h5 className="text-sm font-medium">校对预览</h5>
                            <p className="mt-1 text-xs text-foreground-muted">用于检查换行、错字和可读性。</p>
                          </div>
                          <span className="rounded-md border border-border px-2 py-1 font-mono text-[10px] text-foreground-muted">
                            {selectedEntry.start} - {selectedEntry.end}
                          </span>
                        </div>
                        <div className="rounded-md bg-black px-4 py-6 text-center text-lg font-semibold leading-snug text-white [text-shadow:0_2px_4px_#000,1px_0_#000,-1px_0_#000,0_1px_#000,0_-1px_#000]">
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
          </main>

          <aside className="space-y-4 max-2xl:col-span-2 max-lg:col-span-1">
            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="保存输出" description="留空路径时会自动保存到项目 output 子目录。" />
              <TextField label="文件名" value={fileName} onChange={setFileName} />
              <TextField label="SRT 输出路径（可选）" value={outputPath} placeholder="留空自动生成" onChange={setOutputPath} />
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button onClick={saveSrt} disabled={isSaving || entries.length === 0} className="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  保存 SRT
                </button>
                <button onClick={saveAss} disabled={isSaving || entries.length === 0} className="h-9 rounded-md border border-border px-4 text-sm hover:bg-white/5 disabled:opacity-50">
                  生成 ASS
                </button>
              </div>
              {lastSavedPath && (
                <div className="mt-3 rounded-md border border-success/30 bg-success/10 p-3">
                  <div className="text-xs font-medium text-success">最近保存</div>
                  <p className="mt-1 break-all text-xs text-foreground-muted select-text">{lastSavedPath}</p>
                </div>
              )}
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="校对统计" description="保存前快速检查条目数量、字符数和异常时间。" />
              <div className="mt-4 grid grid-cols-2 gap-2">
                <MetricTile label="字幕条目" value={String(entries.length)} />
                <MetricTile label="正文字符" value={String(totalChars)} />
                <MetricTile label="异常时间" value={String(invalidCount)} tone={invalidCount ? 'warn' : 'default'} />
                <MetricTile label="当前序号" value={entries.length ? String(selectedIndex + 1) : '-'} />
              </div>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <SectionTitle title="纯文本检查" description="只看字幕正文，方便发现重复句和错别字。" />
              <textarea
                value={plainText}
                readOnly
                rows={11}
                className="mt-3 w-full resize-none rounded-md border border-border bg-background-elevated px-3 py-2 text-xs leading-5 text-foreground-muted outline-none"
              />
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

/** 保存前校验字幕条目 */
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

/** 规范字幕序号 */
function normalizeEntries(entries: SubtitleEntry[]) {
  return entries.map((entry, index) => ({
    ...entry,
    index: index + 1,
    start: entry.start || '00:00:00,000',
    end: entry.end || entry.start || '00:00:00,000',
  }))
}

/** 时间码转毫秒，用于前端异常检测 */
function timeToMs(value: string) {
  const parts = value.trim().replace(',', '.').split(':')
  if (parts.length !== 3) return 0
  const hours = Number(parts[0])
  const minutes = Number(parts[1])
  const seconds = Number(parts[2])
  if (![hours, minutes, seconds].every(Number.isFinite)) return 0
  return Math.max(0, Math.round((hours * 3600 + minutes * 60 + seconds) * 1000))
}

/** 毫秒转 SRT 时间码 */
function msToTime(value: number) {
  const safeValue = Math.max(0, Math.round(value))
  const hours = Math.floor(safeValue / 3600000)
  const minutes = Math.floor((safeValue % 3600000) / 60000)
  const seconds = Math.floor((safeValue % 60000) / 1000)
  const millis = safeValue % 1000
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${String(millis).padStart(3, '0')}`
}

/** 两位数补零 */
function pad(value: number) {
  return String(value).padStart(2, '0')
}

/** 分组标题 */
function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h4 className="text-sm font-medium">{title}</h4>
      <p className="mt-1 text-xs text-foreground-muted">{description}</p>
    </div>
  )
}

/** 文本输入 */
function TextField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) {
  return (
    <label className="mt-3 block first:mt-0">
      <span className="mb-1 block text-xs text-foreground-muted">{label}</span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border bg-background-elevated px-3 text-sm outline-none transition-colors focus:border-primary"
      />
    </label>
  )
}

/** 操作反馈 */
function NoticeBox({ notice }: { notice: CorrectionNotice }) {
  if (!notice) return null
  const classes = {
    info: 'border-accent/30 bg-accent/10 text-accent',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    error: 'border-destructive/30 bg-destructive/10 text-destructive',
  }[notice.type]
  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${classes}`} role={notice.type === 'error' ? 'alert' : 'status'}>
      {notice.message}
    </div>
  )
}

/** 指标块 */
function MetricTile({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'warn' }) {
  return (
    <div className="rounded-md border border-border bg-background-elevated p-3">
      <div className="text-[10px] text-foreground-muted">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone === 'warn' ? 'text-warning' : 'text-foreground'}`}>{value}</div>
    </div>
  )
}
