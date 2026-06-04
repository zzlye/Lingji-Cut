// src/features/settings/SettingsWorkspace.tsx
// 设置工作区 - 左侧分组导航 + 右侧内容，取代原先挤在一个弹窗里的 9 个页签
import { Film, Captions, Cpu, Mic, BookMarked, ShieldAlert, FolderCog } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useUiStore, type SettingsSection } from '@/stores/uiStore'
import { EffectsSettingsPanel } from '@/features/effects/EffectsPanel'
import { SubtitleEditor } from '@/features/subtitle/SubtitleEditor'
import { ApiConfigPanel } from './ApiConfigPanel'
import { VoiceConfigPanel } from '@/features/voice/VoiceConfigPanel'
import { GlossaryPanel } from './GlossaryPanel'
import { BannedWordsPanel } from './BannedWordsPanel'
import { FileLocationPanel } from './FileLocationPanel'

/** 设置分组导航 */
const SECTION_GROUPS: Array<{ group: string; items: Array<{ id: SettingsSection; label: string; icon: typeof Film }> }> = [
  { group: '处理', items: [
    { id: 'effects', label: '画面处理', icon: Film },
    { id: 'subtitle', label: '字幕样式', icon: Captions },
  ] },
  { group: '文本与配音', items: [
    { id: 'api', label: '文本 API', icon: Cpu },
    { id: 'voice', label: '配音', icon: Mic },
  ] },
  { group: '内容规则', items: [
    { id: 'glossary', label: '术语表', icon: BookMarked },
    { id: 'banned', label: '禁词表', icon: ShieldAlert },
  ] },
  { group: '系统', items: [
    { id: 'paths', label: '文件位置', icon: FolderCog },
  ] },
]

export function SettingsWorkspace() {
  const section = useUiStore((s) => s.settingsSection)
  const setSection = useUiStore((s) => s.setSettingsSection)

  return (
    <div className="flex h-full min-h-0">
      <nav className="glass w-52 shrink-0 space-y-4 overflow-auto border-r p-3">
        {SECTION_GROUPS.map((g) => (
          <div key={g.group}>
            <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{g.group}</p>
            <div className="space-y-0.5">
              {g.items.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    onClick={() => setSection(item.id)}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                      section === item.id ? 'bg-primary/15 font-medium text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                    )}
                  >
                    <Icon className="size-4" />
                    {item.label}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="min-w-0 flex-1 overflow-auto">
        {section === 'effects' && <EffectsSettingsPanel variant="compact" />}
        {section === 'subtitle' && <SubtitleEditor compact />}
        {section === 'api' && <ApiConfigPanel compact />}
        {section === 'voice' && <VoiceConfigPanel compact />}
        {section === 'glossary' && <GlossaryPanel />}
        {section === 'banned' && <BannedWordsPanel />}
        {section === 'paths' && <FileLocationPanel />}
      </div>
    </div>
  )
}
