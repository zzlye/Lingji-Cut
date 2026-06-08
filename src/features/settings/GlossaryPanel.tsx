// src/features/settings/GlossaryPanel.tsx
// 术语表设置面板 - 管理专业术语、固定写法和备注（shadcn 重做）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { BookMarked, Plus, Search, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import { cn } from '@/lib/utils'
import type { GlossaryTerm } from '@/types'

export function GlossaryPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const [newTerm, setNewTerm] = useState({ source: '', replacement: '', note: '' })
  const [searchKeyword, setSearchKeyword] = useState('')
  const termRefs = useRef<Array<HTMLDivElement | null>>([])
  const terms = preferences.glossary_terms
  const normalizedSearchKeyword = searchKeyword.trim().toLowerCase()
  const matchedTermIndexes = useMemo(() => {
    if (!normalizedSearchKeyword) return []
    return terms.reduce<number[]>((result, term, index) => {
      const searchableText = [term.source, term.replacement, term.note].join('\n').toLowerCase()
      if (searchableText.includes(normalizedSearchKeyword)) {
        result.push(index)
      }
      return result
    }, [])
  }, [normalizedSearchKeyword, terms])
  const activeTermIndex = matchedTermIndexes[0] ?? -1

  useEffect(() => {
    if (!normalizedSearchKeyword || activeTermIndex < 0) return
    termRefs.current[activeTermIndex]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeTermIndex, normalizedSearchKeyword])

  /** 添加术语 */
  const addTerm = () => {
    const source = newTerm.source.trim()
    if (!source) return
    const nextTerm: GlossaryTerm = {
      id: `term_${Date.now()}`,
      source,
      replacement: newTerm.replacement.trim(),
      note: newTerm.note.trim(),
    }
    setPreferences(saveAutomationPreferences({ glossary_terms: [...terms, nextTerm] }))
    setNewTerm({ source: '', replacement: '', note: '' })
  }

  /** 更新术语 */
  const updateTerm = (id: string, patch: Partial<GlossaryTerm>) => {
    setPreferences(saveAutomationPreferences({ glossary_terms: terms.map((t) => (t.id === id ? { ...t, ...patch } : t)) }))
  }

  /** 删除术语 */
  const removeTerm = (id: string) => {
    setPreferences(saveAutomationPreferences({ glossary_terms: terms.filter((t) => t.id !== id) }))
  }

  /** 导入示例 */
  const addExampleTerms = () => {
    const existing = new Set(terms.map((t) => t.source.trim().toLowerCase()))
    const examples: GlossaryTerm[] = [
      { id: `term_${Date.now()}_lol`, source: 'LOL', replacement: '英雄联盟', note: '游戏名，不要翻译成笑' },
      { id: `term_${Date.now()}_dps`, source: 'DPS', replacement: '输出位', note: '游戏职业/定位' },
      { id: `term_${Date.now()}_buff`, source: 'BUFF', replacement: '增益效果', note: '保留口播自然度' },
    ].filter((t) => !existing.has(t.source.toLowerCase()))
    if (examples.length === 0) return
    setPreferences(saveAutomationPreferences({ glossary_terms: [...terms, ...examples] }))
  }

  const handleNewTermKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    addTerm()
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
                placeholder="搜索术语、固定写法或备注，自动跳转到命中项"
                onChange={(event) => setSearchKeyword(event.target.value)}
              />
            </div>
            {normalizedSearchKeyword && (
              <div className="rounded-lg bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
                {matchedTermIndexes.length ? `找到 ${matchedTermIndexes.length} 个匹配，已自动跳到第 1 个。` : '没有找到匹配的术语。'}
              </div>
            )}
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_120px]">
            <Input
              value={newTerm.source}
              className="h-11"
              placeholder="原词，例如 DPS"
              onChange={(event) => setNewTerm((current) => ({ ...current, source: event.target.value }))}
              onKeyDown={handleNewTermKeyDown}
            />
            <Input
              value={newTerm.replacement}
              className="h-11"
              placeholder="固定写法，例如 输出位"
              onChange={(event) => setNewTerm((current) => ({ ...current, replacement: event.target.value }))}
              onKeyDown={handleNewTermKeyDown}
            />
            <Input
              value={newTerm.note}
              className="h-11"
              placeholder="备注，例如 不要翻译成笑"
              onChange={(event) => setNewTerm((current) => ({ ...current, note: event.target.value }))}
              onKeyDown={handleNewTermKeyDown}
            />
            <Button className="h-11 gap-1.5" onClick={addTerm} disabled={!newTerm.source.trim()}>
              <Plus className="size-4" /> 添加
            </Button>
          </div>

          <div className="overflow-hidden rounded-2xl border bg-background/70">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
              <div>
                <p className="flex items-center gap-2 text-sm font-medium"><BookMarked className="size-4" /> 术语列表</p>
                <p className="text-xs text-muted-foreground">共 {terms.length} 条，修改后会自动保存。</p>
              </div>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={addExampleTerms}>
                <Sparkles className="size-4" /> 加入示例
              </Button>
            </div>
            <div className="grid grid-cols-[52px_minmax(82px,0.85fr)_minmax(110px,1fr)_minmax(96px,1fr)_44px] border-b bg-muted/35 px-4 py-2 text-xs font-medium text-muted-foreground">
              <span>序号</span>
              <span>原词</span>
              <span>固定写法</span>
              <span>备注</span>
              <span className="text-right">操作</span>
            </div>
            <div className="min-h-[480px] max-h-[58vh] overflow-auto">
              {terms.length === 0 ? (
                <div className="grid min-h-[480px] place-items-center text-center">
                  <div className="space-y-2">
                    <BookMarked className="mx-auto size-8 text-muted-foreground/50" />
                    <p className="text-sm text-muted-foreground">暂无术语</p>
                    <p className="text-xs text-muted-foreground/70">添加后可把「DPS」固定为「输出位」，或把角色名固定成指定译名</p>
                  </div>
                </div>
              ) : (
                <div>
                  {terms.map((term, index) => (
                    <div
                      key={term.id}
                      ref={(node) => {
                        termRefs.current[index] = node
                      }}
                      className={cn(
                        'grid grid-cols-[52px_minmax(82px,0.85fr)_minmax(110px,1fr)_minmax(96px,1fr)_44px] items-center gap-3 border-b px-4 py-2 transition-colors last:border-b-0',
                        activeTermIndex === index && 'bg-primary/10',
                        normalizedSearchKeyword && matchedTermIndexes.includes(index) && activeTermIndex !== index && 'bg-primary/5',
                      )}
                    >
                      <span className="text-xs font-medium text-muted-foreground">#{index + 1}</span>
                      <Input
                        value={term.source}
                        className="h-9 border-transparent bg-transparent px-0 shadow-none focus-visible:border-input focus-visible:bg-background focus-visible:px-3"
                        placeholder="原词"
                        onChange={(event) => updateTerm(term.id, { source: event.target.value })}
                      />
                      <Input
                        value={term.replacement}
                        className="h-9 border-transparent bg-transparent px-0 shadow-none focus-visible:border-input focus-visible:bg-background focus-visible:px-3"
                        placeholder="固定写法"
                        onChange={(event) => updateTerm(term.id, { replacement: event.target.value })}
                      />
                      <Input
                        value={term.note}
                        className="h-9 border-transparent bg-transparent px-0 shadow-none focus-visible:border-input focus-visible:bg-background focus-visible:px-3"
                        placeholder="备注"
                        onChange={(event) => updateTerm(term.id, { note: event.target.value })}
                      />
                      <div className="flex justify-end">
                        <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={() => removeTerm(term.id)} aria-label="删除术语">
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
