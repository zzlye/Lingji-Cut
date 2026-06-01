// src/features/settings/GlossaryPanel.tsx
// 术语表设置面板 - 管理专业术语、固定写法和备注

import { useState } from 'react'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'
import type { GlossaryTerm } from '@/types'

/**
 * 术语表面板
 * 专门管理游戏、品牌、人名等专业词，供字幕处理和配音文本复用。
 */
export function GlossaryPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())

  /** 添加专业术语 */
  const addTerm = () => {
    const nextTerm: GlossaryTerm = {
      id: `term_${Date.now()}`,
      source: '',
      replacement: '',
      note: '',
    }
    setPreferences(saveAutomationPreferences({
      glossary_terms: [...preferences.glossary_terms, nextTerm],
    }))
  }

  /** 更新专业术语 */
  const updateTerm = (id: string, patch: Partial<GlossaryTerm>) => {
    setPreferences(saveAutomationPreferences({
      glossary_terms: preferences.glossary_terms.map((term) => (
        term.id === id ? { ...term, ...patch } : term
      )),
    }))
  }

  /** 删除专业术语 */
  const removeTerm = (id: string) => {
    setPreferences(saveAutomationPreferences({
      glossary_terms: preferences.glossary_terms.filter((term) => term.id !== id),
    }))
  }

  /** 导入示例词，方便用户理解字段含义 */
  const addExampleTerms = () => {
    const existing = new Set(preferences.glossary_terms.map((term) => term.source.trim().toLowerCase()))
    const examples: GlossaryTerm[] = [
      { id: `term_${Date.now()}_lol`, source: 'LOL', replacement: '英雄联盟', note: '游戏名，不要翻译成笑' },
      { id: `term_${Date.now()}_dps`, source: 'DPS', replacement: '输出位', note: '游戏职业/定位' },
      { id: `term_${Date.now()}_buff`, source: 'BUFF', replacement: '增益效果', note: '保留口播自然度' },
    ].filter((term) => !existing.has(term.source.toLowerCase()))
    if (examples.length === 0) return
    setPreferences(saveAutomationPreferences({
      glossary_terms: [...preferences.glossary_terms, ...examples],
    }))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-medium">术语表</h3>
            <p className="mt-1 text-xs text-foreground-muted">固定游戏、品牌、人名和专有名词写法，自动流程会传给字幕处理和配音文本。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={addExampleTerms} className="h-8 rounded-md border border-border px-3 text-xs hover:bg-white/5">
              加入示例
            </button>
            <button onClick={addTerm} className="h-8 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90">
              添加术语
            </button>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(0,1fr)_240px] gap-4 max-lg:grid-cols-1">
          <main className="space-y-3">
            {preferences.glossary_terms.length === 0 && (
              <div className="rounded-lg border border-dashed border-border bg-background p-8 text-center">
                <p className="text-sm font-medium">暂无术语</p>
                <p className="mt-1 text-xs text-foreground-muted">添加后可把“DPS”固定为“输出位”，或把角色名固定成指定译名。</p>
              </div>
            )}

            {preferences.glossary_terms.map((term, index) => (
              <article key={term.id} className="rounded-lg border border-border bg-background p-4">
                <div className="mb-3 flex items-center justify-between gap-3 border-b border-border pb-3">
                  <div>
                    <h4 className="text-sm font-medium">术语 #{index + 1}</h4>
                    <p className="text-xs text-foreground-muted">原词命中后会替换或提示为固定写法。</p>
                  </div>
                  <button onClick={() => removeTerm(term.id)} className="h-8 rounded-md border border-border px-3 text-xs text-destructive hover:bg-white/5">
                    删除
                  </button>
                </div>
                <div className="grid grid-cols-[minmax(140px,1fr)_minmax(140px,1fr)_minmax(180px,1.4fr)] gap-3 max-xl:grid-cols-1">
                  <TextField label="原词" value={term.source} placeholder="例如 DPS" onChange={(value) => updateTerm(term.id, { source: value })} />
                  <TextField label="固定写法" value={term.replacement} placeholder="例如 输出位" onChange={(value) => updateTerm(term.id, { replacement: value })} />
                  <TextField label="备注" value={term.note} placeholder="发音、场景、不要翻译等" onChange={(value) => updateTerm(term.id, { note: value })} />
                </div>
              </article>
            ))}
          </main>

          <aside className="space-y-3">
            <MetricCard label="术语数量" value={String(preferences.glossary_terms.length)} />
            <InfoCard
              title="使用位置"
              lines={[
                '字幕生成、翻译、润色时作为提示词补充。',
                '字幕和配音文本会优先套用固定写法。',
                '空原词不会保存到自动流程参数。',
              ]}
            />
          </aside>
        </div>
      </div>
    </div>
  )
}

/** 文本输入 */
function TextField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
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

/** 指标卡片 */
function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <div className="text-xs text-foreground-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  )
}

/** 说明卡片 */
function InfoCard({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <h4 className="text-sm font-medium">{title}</h4>
      <ul className="mt-3 space-y-2 text-xs text-foreground-muted">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  )
}
