// src/features/settings/BannedWordsPanel.tsx
// 禁词表设置面板 - 管理自动流程中的禁词提醒和拦截策略（shadcn 重做）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { Plus, Search, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { SegmentedField } from '@/components/fields'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import { cn } from '@/lib/utils'

export function BannedWordsPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const [draftWords, setDraftWords] = useState(() => preferences.banned_words)
  const [newWord, setNewWord] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
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
  const activeWordIndex = matchedWordIndexes[0] ?? -1

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

  return (
    <div className="h-full p-6">
      <Card className="mx-auto max-w-5xl overflow-hidden">
        <CardContent className="space-y-4 p-5">
          <div className="space-y-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchKeyword}
                className="h-11 pl-9"
                placeholder="搜索禁词，自动跳转到命中项"
                onChange={(event) => setSearchKeyword(event.target.value)}
              />
            </div>
            {normalizedSearchKeyword && (
              <div className="rounded-lg bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
                {matchedWordIndexes.length ? `找到 ${matchedWordIndexes.length} 个匹配，已自动跳到第 1 个。` : '没有找到匹配的禁词。'}
              </div>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_120px]">
            <Input
              value={newWord}
              className="h-11"
              placeholder="输入禁词，可一次粘贴多行或用逗号分隔"
              onChange={(event) => setNewWord(event.target.value)}
              onKeyDown={handleNewWordKeyDown}
            />
            <Button className="h-11 gap-1.5" onClick={addWords} disabled={!newWord.trim()}>
              <Plus className="size-4" /> 添加
            </Button>
          </div>

          <div className="overflow-hidden rounded-2xl border bg-background/70">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
              <div>
                <p className="flex items-center gap-2 text-sm font-medium"><ShieldAlert className="size-4" /> 禁词列表</p>
                <p className="text-xs text-muted-foreground">共 {words.length} 个，修改后会自动保存。</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">命中策略</span>
                <SegmentedField value={preferences.banned_word_action} options={[['warn', '提醒继续'], ['block', '命中停止']]} onChange={updateAction} />
              </div>
            </div>
            <div className="grid grid-cols-[64px_minmax(0,1fr)_64px] border-b bg-muted/35 px-4 py-2 text-xs font-medium text-muted-foreground">
              <span>序号</span>
              <span>禁词</span>
              <span className="text-right">操作</span>
            </div>
            <div className="min-h-[480px] max-h-[58vh] overflow-auto">
              {words.length === 0 ? (
                <div className="grid min-h-[480px] place-items-center text-center">
                  <div className="space-y-2">
                    <ShieldAlert className="mx-auto size-8 text-muted-foreground/50" />
                    <p className="text-sm text-muted-foreground">暂无禁词</p>
                    <p className="text-xs text-muted-foreground/70">添加后会在字幕和配音文案里检测命中内容</p>
                  </div>
                </div>
              ) : (
                <div>
                  {words.map((word, index) => (
                    <div
                      key={`${index}-${word}`}
                      ref={(node) => {
                        wordRefs.current[index] = node
                      }}
                      className={cn(
                        'grid grid-cols-[64px_minmax(0,1fr)_64px] items-center gap-3 border-b px-4 py-2 transition-colors last:border-b-0',
                        activeWordIndex === index && 'bg-primary/10',
                        normalizedSearchKeyword && matchedWordIndexes.includes(index) && activeWordIndex !== index && 'bg-primary/5',
                      )}
                    >
                      <span className="text-xs font-medium text-muted-foreground">#{index + 1}</span>
                      <Input
                        value={word}
                        className="h-9 border-transparent bg-transparent px-0 shadow-none focus-visible:border-input focus-visible:bg-background focus-visible:px-3"
                        placeholder="输入禁词"
                        onChange={(event) => updateDraftWord(index, event.target.value)}
                        onBlur={commitDraftWords}
                        onKeyDown={handleWordKeyDown}
                      />
                      <div className="flex justify-end">
                        <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={() => removeWord(index)} aria-label="删除禁词">
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
