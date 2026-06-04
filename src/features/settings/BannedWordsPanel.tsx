// src/features/settings/BannedWordsPanel.tsx
// 禁词表设置面板 - 管理自动流程中的禁词提醒和拦截策略（shadcn 重做）
import { useMemo, useState } from 'react'
import { ShieldAlert, X } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { SegmentedField, TextareaField } from '@/components/fields'
import { loadAutomationPreferences, saveAutomationPreferences } from '@/lib/automationPreferences'

export function BannedWordsPanel() {
  const [preferences, setPreferences] = useState(() => loadAutomationPreferences())
  const [draft, setDraft] = useState(() => preferences.banned_words.join('\n'))
  const words = useMemo(
    () => Array.from(new Set(draft.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))),
    [draft],
  )

  /** 保存禁词 */
  const saveWords = (value: string) => {
    setDraft(value)
    setPreferences(saveAutomationPreferences({
      banned_words: Array.from(new Set(value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))),
    }))
  }

  /** 切换命中策略 */
  const updateAction = (value: string) => {
    setPreferences(saveAutomationPreferences({ banned_word_action: value as 'warn' | 'block' }))
  }

  /** 删除单个禁词 */
  const removeWord = (word: string) => saveWords(words.filter((item) => item !== word).join('\n'))

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">禁词表</h2>
        <p className="text-sm text-muted-foreground">检测字幕和配音文案中的禁词，支持命中后提醒继续或停止整个流程。</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm">
            <span>命中策略</span>
            <SegmentedField
              value={preferences.banned_word_action}
              options={[['warn', '提醒继续'], ['block', '命中停止']]}
              onChange={updateAction}
            />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <TextareaField
            label="禁词列表"
            value={draft}
            rows={10}
            placeholder="每行一个禁词，也可以用逗号分隔…"
            description="保存会自动去重和清理空行"
            onChange={saveWords}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2"><ShieldAlert className="size-4" /> 当前禁词</span>
            <span className="text-xs font-normal text-muted-foreground">{words.length} 个</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {words.length === 0 ? (
            <p className="text-xs text-muted-foreground">暂无禁词，自动流程不会做禁词检查。</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {words.map((word) => (
                <Badge key={word} variant="secondary" className="cursor-pointer gap-1 hover:bg-destructive hover:text-white" onClick={() => removeWord(word)}>
                  {word}
                  <X className="size-3" />
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
