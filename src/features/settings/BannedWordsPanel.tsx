// src/features/settings/BannedWordsPanel.tsx
// 禁词表设置面板 - 管理自动流程中的禁词提醒和拦截策略（shadcn 重做）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ChevronDown, ChevronUp, Plus, Search, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Field, SegmentedField } from '@/components/fields'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import { cn } from '@/lib/utils'

export function BannedWordsPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const [draftWords, setDraftWords] = useState(() => preferences.banned_words)
  const [newWord, setNewWord] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [activeMatchPosition, setActiveMatchPosition] = useState(0)
  const wordRefs = useRef<Array<HTMLDivElement | null>>([])
  const normalizedSearchKeyword = searchKeyword.trim().toLowerCase()
  const words = draftWords
  const matchedWordIndexes = useMemo(() => {
    if (!normalizedSearchKeyword) return []
    return words.reduce<number[]>((result, word, index) => {
      if (word.toLowerCase().includes(normalizedSearchKeyword)) {
        result.push(index)
      }
      return result
    }, [])
  }, [normalizedSearchKeyword, words])
  const activeWordIndex = matchedWordIndexes[activeMatchPosition] ?? -1

  useEffect(() => {
    setActiveMatchPosition(0)
  }, [normalizedSearchKeyword])

  useEffect(() => {
    if (activeMatchPosition < matchedWordIndexes.length) return
    setActiveMatchPosition(Math.max(0, matchedWordIndexes.length - 1))
  }, [activeMatchPosition, matchedWordIndexes.length])

  useEffect(() => {
    if (!normalizedSearchKeyword || activeWordIndex < 0) return
    wordRefs.current[activeWordIndex]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeWordIndex, normalizedSearchKeyword])

  /** 规范禁词：支持粘贴多行或逗号分隔，同时保持原有顺序并去重 */
  const normalizeWords = (value: string[]) => {
    const seen = new Set<string>()
    return value
      .flatMap((item) => item.split(/\r?\n|,/))
      .map((item) => item.trim())
      .filter((item) => {
        if (!item) return false
        const key = item.toLowerCase()
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
  }

  /** 保存禁词 */
  const saveWords = (nextWords: string[]) => {
    const normalizedWords = normalizeWords(nextWords)
    setDraftWords(normalizedWords)
    setPreferences(saveAutomationPreferences({ banned_words: normalizedWords }))
  }

  /** 切换命中策略 */
  const updateAction = (value: string) => {
    setPreferences(saveAutomationPreferences({ banned_word_action: value as 'warn' | 'block' }))
  }

  /** 添加禁词，输入框支持一次粘贴多个 */
  const addWords = () => {
    const normalizedWords = normalizeWords([...words, newWord])
    if (normalizedWords.length === words.length) {
      setNewWord('')
      return
    }
    setNewWord('')
    saveWords(normalizedWords)
  }

  /** 修改单个禁词，失焦或回车后才写入配置，避免输入中途被去重打断 */
  const updateDraftWord = (index: number, value: string) => {
    setDraftWords((current) => current.map((word, itemIndex) => (itemIndex === index ? value : word)))
  }

  /** 提交当前列表里的编辑结果 */
  const commitDraftWords = () => saveWords(draftWords)

  /** 删除单个禁词 */
  const removeWord = (index: number) => saveWords(words.filter((_, itemIndex) => itemIndex !== index))

  const handleNewWordKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    addWords()
  }

  const handleWordKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    commitDraftWords()
    event.currentTarget.blur()
  }

  const jumpMatch = (direction: 'previous' | 'next') => {
    if (!matchedWordIndexes.length) return
    setActiveMatchPosition((current) => {
      const offset = direction === 'next' ? 1 : -1
      return (current + offset + matchedWordIndexes.length) % matchedWordIndexes.length
    })
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">禁词表</h2>
          <p className="text-sm text-muted-foreground">检测字幕和配音文案中的禁词，支持命中后提醒继续或停止整个流程。</p>
        </div>
        <Badge variant="secondary">{words.length} 个禁词</Badge>
      </div>

      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">命中策略</p>
              <p className="text-xs text-muted-foreground">提醒继续适合人工确认，命中停止适合无人值守流程。</p>
            </div>
            <SegmentedField
              value={preferences.banned_word_action}
              options={[['warn', '提醒继续'], ['block', '命中停止']]}
              onChange={updateAction}
            />
          </div>

          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <Field label="新增禁词" description="可粘贴多行或用逗号分隔，保存时会自动去重。">
              <Input
                value={newWord}
                placeholder="输入禁词，例如 敏感词A"
                onChange={(event) => setNewWord(event.target.value)}
                onKeyDown={handleNewWordKeyDown}
              />
            </Field>
            <Button className="mt-7 gap-1.5 md:self-start" onClick={addWords} disabled={!newWord.trim()}>
              <Plus className="size-4" /> 添加
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="flex items-center gap-2 text-sm font-medium"><ShieldAlert className="size-4" /> 当前禁词</p>
              <p className="text-xs text-muted-foreground">像术语表一样逐条管理，搜索会直接跳到命中的禁词。</p>
            </div>
            <div className="flex min-w-[280px] flex-1 items-center gap-2 md:max-w-md">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchKeyword}
                  className="pl-9"
                  placeholder="搜索禁词并跳转"
                  onChange={(event) => setSearchKeyword(event.target.value)}
                />
              </div>
              <Button variant="outline" size="icon-sm" onClick={() => jumpMatch('previous')} disabled={!matchedWordIndexes.length} aria-label="上一个禁词">
                <ChevronUp className="size-4" />
              </Button>
              <Button variant="outline" size="icon-sm" onClick={() => jumpMatch('next')} disabled={!matchedWordIndexes.length} aria-label="下一个禁词">
                <ChevronDown className="size-4" />
              </Button>
            </div>
          </div>

          {normalizedSearchKeyword && (
            <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              {matchedWordIndexes.length
                ? `已找到 ${matchedWordIndexes.length} 个匹配，当前第 ${activeMatchPosition + 1} 个。`
                : '没有找到匹配的禁词。'}
            </div>
          )}

          {words.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
              <ShieldAlert className="size-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">暂无禁词</p>
              <p className="text-xs text-muted-foreground/70">添加后会在字幕和配音文案里检测命中内容</p>
            </div>
          ) : (
            <div className="space-y-3">
              {words.map((word, index) => (
                <div
                  key={`${index}-${word}`}
                  ref={(node) => {
                    wordRefs.current[index] = node
                  }}
                >
                  <Card className={cn(
                    'transition-colors',
                    activeWordIndex === index && 'border-primary bg-primary/10 shadow-sm',
                    normalizedSearchKeyword && matchedWordIndexes.includes(index) && activeWordIndex !== index && 'border-primary/40',
                  )}>
                    <CardContent className="space-y-3 pt-6">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-muted-foreground">禁词 #{index + 1}</span>
                          {activeWordIndex === index && <Badge>当前命中</Badge>}
                        </div>
                        <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={() => removeWord(index)} aria-label="删除禁词">
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                      <Field label="禁词内容">
                        <Input
                          value={word}
                          placeholder="输入禁词"
                          onChange={(event) => updateDraftWord(index, event.target.value)}
                          onBlur={commitDraftWords}
                          onKeyDown={handleWordKeyDown}
                        />
                      </Field>
                    </CardContent>
                  </Card>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
