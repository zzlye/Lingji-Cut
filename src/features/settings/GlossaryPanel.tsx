// src/features/settings/GlossaryPanel.tsx
// 术语表设置面板 - 管理专业术语、固定写法和备注（shadcn 重做）
import { useState } from 'react'
import { Plus, Sparkles, Trash2, BookMarked } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { TextField } from '@/components/fields'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { GlossaryTerm } from '@/types'

export function GlossaryPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const terms = preferences.glossary_terms

  /** 添加术语 */
  const addTerm = () => {
    const nextTerm: GlossaryTerm = { id: `term_${Date.now()}`, source: '', replacement: '', note: '' }
    setPreferences(saveAutomationPreferences({ glossary_terms: [...terms, nextTerm] }))
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

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">术语表</h2>
          <p className="text-sm text-muted-foreground">固定游戏、品牌、人名等专有名词写法，自动流程会传给字幕处理和配音文本。</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={addExampleTerms}><Sparkles className="size-4" /> 加入示例</Button>
          <Button size="sm" className="gap-1.5" onClick={addTerm}><Plus className="size-4" /> 添加术语</Button>
        </div>
      </div>

      {terms.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
          <BookMarked className="size-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">暂无术语</p>
          <p className="text-xs text-muted-foreground/70">添加后可把「DPS」固定为「输出位」，或把角色名固定成指定译名</p>
        </div>
      ) : (
        <div className="space-y-3">
          {terms.map((term, index) => (
            <Card key={term.id}>
              <CardContent className="space-y-3 pt-6">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-muted-foreground">术语 #{index + 1}</span>
                  <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={() => removeTerm(term.id)} aria-label="删除"><Trash2 className="size-4" /></Button>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <TextField label="原词" value={term.source} placeholder="例如 DPS" onChange={(v) => updateTerm(term.id, { source: v })} />
                  <TextField label="固定写法" value={term.replacement} placeholder="例如 输出位" onChange={(v) => updateTerm(term.id, { replacement: v })} />
                  <TextField label="备注" value={term.note} placeholder="发音、场景、不要翻译等" onChange={(v) => updateTerm(term.id, { note: v })} />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
