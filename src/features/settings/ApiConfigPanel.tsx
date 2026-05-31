// src/features/settings/ApiConfigPanel.tsx
// API 配置面板 - 管理文本 API 配置

import { useState, useEffect } from 'react'
import { profileApi } from '@/lib/api'
import type { ApiProfile } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/**
 * API 配置面板
 * 支持添加、查看、测试文本 API 配置
 */
export function ApiConfigPanel({ compact = false }: { compact?: boolean }) {
  const [profiles, setProfiles] = useState<ApiProfile[]>([])
  const [isAdding, setIsAdding] = useState(false)
  const { addLog } = useTaskStore()

  // 表单状态
  const [form, setForm] = useState({
    name: '',
    provider_type: 'openai',
    base_url: '',
    api_key: '',
    model: '',
  })

  /** 加载配置列表 */
  const loadProfiles = async () => {
    try {
      const data = await profileApi.listText()
      setProfiles(data)
    } catch (error) {
      addLog('error', `加载配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadProfiles()
  }, [])

  /** 提交表单 */
  const handleSubmit = async () => {
    if (!form.name || !form.base_url || !form.api_key) {
      addLog('warn', '请填写必要字段')
      return
    }

    try {
      await profileApi.createText(form)
      addLog('info', `配置 "${form.name}" 已保存`)
      setIsAdding(false)
      setForm({ name: '', provider_type: 'openai', base_url: '', api_key: '', model: '' })
      loadProfiles()
    } catch (error) {
      addLog('error', `保存配置失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 测试连接 */
  const handleTest = async (id: number) => {
    try {
      const result = await profileApi.test('text', id)
      addLog('info', result.message)
    } catch (error) {
      addLog('error', `测试失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {!compact && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-medium">API 配置</h3>
        </div>
      )}

      {/* 内容区域 */}
      <div className="flex-1 overflow-auto p-4">
        {/* 添加按钮 */}
        <button
          onClick={() => setIsAdding(!isAdding)}
          className="w-full h-9 mb-4 border border-dashed border-border rounded-md text-sm text-foreground-muted hover:text-foreground hover:border-primary transition-colors"
        >
          + 添加配置
        </button>

        {/* 添加表单 */}
        {isAdding && (
          <div className="mb-4 p-4 bg-background-elevated rounded-lg border border-border space-y-3">
            <input
              type="text"
              placeholder="配置名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            />
            <select
              value={form.provider_type}
              onChange={(e) => setForm({ ...form, provider_type: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            >
              <option value="openai">OpenAI</option>
              <option value="openai_compatible">OpenAI Compatible</option>
              <option value="gemini">Gemini</option>
              <option value="gemini_compatible">Gemini Compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="custom">自定义</option>
            </select>
            <input
              type="text"
              placeholder="Base URL"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            />
            <input
              type="password"
              placeholder="API Key"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            />
            <input
              type="text"
              placeholder="模型名称（可选）"
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            />
            <div className="flex gap-2">
              <button
                onClick={handleSubmit}
                className="flex-1 h-9 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90"
              >
                保存
              </button>
              <button
                onClick={() => setIsAdding(false)}
                className="h-9 px-4 border border-border rounded-md text-sm hover:bg-white/5"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 配置列表 */}
        <div className="space-y-2">
          {profiles.map((profile) => (
            <div
              key={profile.id}
              className="p-3 bg-background-elevated rounded-lg border border-border"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-sm">{profile.name}</span>
                <button
                  onClick={() => handleTest(profile.id)}
                  className="px-2 py-1 text-xs border border-border rounded hover:bg-white/5"
                >
                  测试
                </button>
              </div>
              <p className="text-xs text-foreground-muted">{profile.provider_type}</p>
              {profile.model && (
                <p className="text-xs text-foreground-muted">模型: {profile.model}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
