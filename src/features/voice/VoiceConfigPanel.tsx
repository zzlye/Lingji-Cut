// src/features/voice/VoiceConfigPanel.tsx
// 配音配置面板 - 管理配音参数

import { useState } from 'react'
import { voiceApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'

/**
 * 配音配置面板
 * 配置配音参数和测试配音生成
 */
export function VoiceConfigPanel({ compact = false }: { compact?: boolean }) {
  const [text, setText] = useState('')
  const [profileId, setProfileId] = useState(1)
  const [voice, setVoice] = useState('alloy')
  const [isGenerating, setIsGenerating] = useState(false)
  const { addLog } = useTaskStore()

  /** 生成配音 */
  const handleGenerate = async () => {
    if (!text.trim()) {
      addLog('warn', '请输入配音文本')
      return
    }

    setIsGenerating(true)
    addLog('info', '开始生成配音...')

    try {
      const result = await voiceApi.generate(text, profileId, voice)
      addLog('info', `配音生成成功: ${result.output_path}`)
    } catch (error) {
      addLog('error', `配音生成失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {!compact && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-medium">配音配置</h3>
        </div>
      )}

      <div className="flex-1 overflow-auto p-4 space-y-4">
        {/* 配置说明 */}
        <div className="p-3 bg-background-elevated rounded-lg border border-border">
          <p className="text-sm text-foreground-muted">
            在此处配置配音参数。首先在 API 配置中添加配音 API 配置，然后在此处选择配置并测试配音生成。
          </p>
        </div>

        {/* 配置 ID */}
        <div>
          <label className="text-xs text-foreground-muted mb-1 block">配音配置 ID</label>
          <input
            type="number"
            value={profileId}
            onChange={(e) => setProfileId(Number(e.target.value))}
            className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
          />
        </div>

        {/* 语音选择 */}
        <div>
          <label className="text-xs text-foreground-muted mb-1 block">语音</label>
          <select
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
          >
            <option value="alloy">Alloy</option>
            <option value="echo">Echo</option>
            <option value="fable">Fable</option>
            <option value="onyx">Onyx</option>
            <option value="nova">Nova</option>
            <option value="shimmer">Shimmer</option>
          </select>
        </div>

        {/* 输入文本 */}
        <div>
          <label className="text-xs text-foreground-muted mb-1 block">配音文本</label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="输入要生成配音的文本..."
            rows={6}
            className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm resize-none"
          />
        </div>

        {/* 生成按钮 */}
        <button
          onClick={handleGenerate}
          disabled={isGenerating || !text.trim()}
          className="w-full h-10 bg-primary text-primary-foreground rounded-md font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isGenerating ? '生成中...' : '测试生成配音'}
        </button>

        {/* 使用说明 */}
        <div className="text-xs text-foreground-muted space-y-1">
          <p>使用说明：</p>
          <ul className="list-disc list-inside space-y-0.5">
            <li>首先在 API 配置中添加配音 API 配置</li>
            <li>配置 ID 为 API 配置中对应配置的 ID</li>
            <li>支持 OpenAI TTS 和 Gemini TTS</li>
            <li>生成的音频文件将保存到 output 目录</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
