// src/features/settings/BannedWordsPanel.tsx
// 禁词表设置面板 - 管理自动流程中的禁词提醒和拦截策略

import { useMemo, useState } from 'react'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'

/**
 * 禁词表面板
 * 独立管理敏感词、禁用词和命中策略，避免混在字幕样式设置里。
 */
export function BannedWordsPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const [draft, setDraft] = useState(() => preferences.banned_words.join('\n'))
  const words = useMemo(
    () => Array.from(new Set(draft.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))),
    [draft],
  )

  /** 保存禁词文本框 */
  const saveWords = (value: string) => {
    setDraft(value)
    setPreferences(saveAutomationPreferences({
      banned_words: Array.from(new Set(value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))),
    }))
  }

  /** 切换命中策略 */
  const updateAction = (value: 'warn' | 'block') => {
    setPreferences(saveAutomationPreferences({ banned_word_action: value }))
  }

  /** 删除单个禁词 */
  const removeWord = (word: string) => {
    saveWords(words.filter((item) => item !== word).join('\n'))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-medium">禁词表</h3>
            <p className="mt-1 text-xs text-foreground-muted">检测字幕和配音文案中的禁词，支持提醒继续或命中停止。</p>
          </div>
          <div className="flex rounded-md border border-border bg-background p-1">
            <button
              onClick={() => updateAction('warn')}
              className={`h-7 rounded px-3 text-xs ${preferences.banned_word_action === 'warn' ? 'bg-warning text-background' : 'text-foreground-muted hover:text-foreground'}`}
            >
              提醒继续
            </button>
            <button
              onClick={() => updateAction('block')}
              className={`h-7 rounded px-3 text-xs ${preferences.banned_word_action === 'block' ? 'bg-destructive text-white' : 'text-foreground-muted hover:text-foreground'}`}
            >
              命中停止
            </button>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="grid grid-cols-[minmax(0,1fr)_260px] gap-4 max-lg:grid-cols-1">
          <main className="space-y-4">
            <section className="rounded-lg border border-border bg-background p-4">
              <label className="block">
                <span className="mb-1 block text-xs text-foreground-muted">禁词列表</span>
                <textarea
                  value={draft}
                  onChange={(event) => saveWords(event.target.value)}
                  rows={14}
                  placeholder="每行一个禁词，也可以用逗号分隔..."
                  className="w-full resize-y rounded-md border border-border bg-background-elevated px-3 py-2 text-sm outline-none transition-colors focus:border-primary"
                />
              </label>
              <p className="mt-2 text-xs text-foreground-muted">保存会自动去重和清理空行。</p>
            </section>

            <section className="rounded-lg border border-border bg-background p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h4 className="text-sm font-medium">当前禁词</h4>
                <span className="text-xs text-foreground-muted">{words.length} 个</span>
              </div>
              {words.length === 0 ? (
                <div className="rounded-md border border-dashed border-border bg-background-elevated p-5 text-center text-xs text-foreground-muted">
                  暂无禁词，自动流程不会做禁词提醒。
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {words.map((word) => (
                    <button
                      key={word}
                      onClick={() => removeWord(word)}
                      className="rounded-md border border-border bg-background-elevated px-3 py-1.5 text-xs hover:border-destructive hover:text-destructive"
                      title="点击删除"
                    >
                      {word}
                    </button>
                  ))}
                </div>
              )}
            </section>
          </main>

          <aside className="space-y-3">
            <MetricCard label="禁词数量" value={String(words.length)} tone={words.length ? 'warn' : 'default'} />
            <MetricCard label="命中策略" value={preferences.banned_word_action === 'block' ? '命中停止' : '提醒继续'} tone={preferences.banned_word_action === 'block' ? 'danger' : 'warn'} />
            <div className="rounded-lg border border-border bg-background p-4">
              <h4 className="text-sm font-medium">处理规则</h4>
              <div className="mt-3 space-y-2 text-xs text-foreground-muted">
                <p>字幕处理后会检查命中词。</p>
                <p>配音文案生成前也会检查命中词。</p>
                <p>“命中停止”会阻止后续自动流程继续。</p>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}

/** 指标卡片 */
function MetricCard({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'warn' | 'danger' }) {
  const valueClass = tone === 'danger' ? 'text-destructive' : tone === 'warn' ? 'text-warning' : 'text-foreground'
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <div className="text-xs text-foreground-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${valueClass}`}>{value}</div>
    </div>
  )
}
