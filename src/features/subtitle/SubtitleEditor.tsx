// src/features/subtitle/SubtitleEditor.tsx
// 字幕预设配置面板 - 管理字幕样式预设

import { useState, useEffect } from 'react'
import { subtitleApi } from '@/lib/api'
import type { SubtitlePreset } from '@/types'
import { useTaskStore } from '@/stores/taskStore'

/**
 * 字幕预设配置面板
 * 支持添加、编辑、删除字幕样式预设
 */
export function SubtitleEditor() {
  const [presets, setPresets] = useState<SubtitlePreset[]>([])
  const [isAdding, setIsAdding] = useState(false)
  const { addLog } = useTaskStore()

  // 表单状态
  const [form, setForm] = useState({
    name: '',
    line_mode: 'double' as 'single' | 'double',
    font_name: 'Microsoft YaHei',
    font_size: 48,
    font_color: '#FFFFFF',
    outline_color: '#000000',
    outline_width: 2,
    position: 'bottom' as 'bottom' | 'top' | 'center',
    margin_v: 30,
  })

  /** 加载预设列表 */
  const loadPresets = async () => {
    try {
      const data = await subtitleApi.listPresets()
      setPresets(data)
    } catch (error) {
      addLog('error', `加载字幕预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    loadPresets()
  }, [])

  /** 提交表单 */
  const handleSubmit = async () => {
    if (!form.name) {
      addLog('warn', '请输入预设名称')
      return
    }

    try {
      await subtitleApi.createPreset(form)
      addLog('info', `字幕预设 "${form.name}" 已保存`)
      setIsAdding(false)
      setForm({
        name: '',
        line_mode: 'double',
        font_name: 'Microsoft YaHei',
        font_size: 48,
        font_color: '#FFFFFF',
        outline_color: '#000000',
        outline_width: 2,
        position: 'bottom',
        margin_v: 30,
      })
      loadPresets()
    } catch (error) {
      addLog('error', `保存预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  /** 删除预设 */
  const handleDelete = async (id: number) => {
    try {
      await subtitleApi.deletePreset(id)
      addLog('info', '预设已删除')
      loadPresets()
    } catch (error) {
      addLog('error', `删除预设失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-medium">字幕预设</h3>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {/* 添加按钮 */}
        <button
          onClick={() => setIsAdding(!isAdding)}
          className="w-full h-9 mb-4 border border-dashed border-border rounded-md text-sm text-foreground-muted hover:text-foreground hover:border-primary transition-colors"
        >
          + 添加预设
        </button>

        {/* 添加表单 */}
        {isAdding && (
          <div className="mb-4 p-4 bg-background-elevated rounded-lg border border-border space-y-3">
            <input
              type="text"
              placeholder="预设名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
            />

            <div className="grid grid-cols-2 gap-3">
              {/* 行模式 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">行模式</label>
                <select
                  value={form.line_mode}
                  onChange={(e) => setForm({ ...form, line_mode: e.target.value as 'single' | 'double' })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                >
                  <option value="single">单行</option>
                  <option value="double">双行</option>
                </select>
              </div>

              {/* 位置 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">位置</label>
                <select
                  value={form.position}
                  onChange={(e) => setForm({ ...form, position: e.target.value as 'bottom' | 'top' | 'center' })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                >
                  <option value="bottom">底部</option>
                  <option value="top">顶部</option>
                  <option value="center">居中</option>
                </select>
              </div>

              {/* 字体 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">字体</label>
                <input
                  type="text"
                  value={form.font_name}
                  onChange={(e) => setForm({ ...form, font_name: e.target.value })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                />
              </div>

              {/* 字号 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">字号</label>
                <input
                  type="number"
                  value={form.font_size}
                  onChange={(e) => setForm({ ...form, font_size: Number(e.target.value) })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                />
              </div>

              {/* 字体颜色 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">字体颜色</label>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={form.font_color}
                    onChange={(e) => setForm({ ...form, font_color: e.target.value })}
                    className="w-9 h-9 rounded cursor-pointer"
                  />
                  <span className="text-xs">{form.font_color}</span>
                </div>
              </div>

              {/* 描边颜色 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">描边颜色</label>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={form.outline_color}
                    onChange={(e) => setForm({ ...form, outline_color: e.target.value })}
                    className="w-9 h-9 rounded cursor-pointer"
                  />
                  <span className="text-xs">{form.outline_color}</span>
                </div>
              </div>

              {/* 描边宽度 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">描边宽度</label>
                <input
                  type="number"
                  value={form.outline_width}
                  onChange={(e) => setForm({ ...form, outline_width: Number(e.target.value) })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                />
              </div>

              {/* 边距 */}
              <div>
                <label className="text-xs text-foreground-muted mb-1 block">边距</label>
                <input
                  type="number"
                  value={form.margin_v}
                  onChange={(e) => setForm({ ...form, margin_v: Number(e.target.value) })}
                  className="w-full h-9 px-3 bg-background border border-border rounded-md text-sm"
                />
              </div>
            </div>

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

        {/* 预设列表 */}
        <div className="space-y-2">
          {presets.map((preset) => (
            <div
              key={preset.id}
              className="p-3 bg-background-elevated rounded-lg border border-border"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-sm">{preset.name}</span>
                <button
                  onClick={() => handleDelete(preset.id)}
                  className="text-xs text-destructive hover:underline"
                >
                  删除
                </button>
              </div>
              <div className="flex items-center gap-3 text-xs text-foreground-muted">
                <span>{preset.line_mode === 'single' ? '单行' : '双行'}</span>
                <span>{preset.font_name}</span>
                <span>{preset.font_size}px</span>
                <span
                  className="w-3 h-3 rounded-sm border border-border inline-block"
                  style={{ backgroundColor: preset.font_color }}
                />
                <span>{preset.position === 'bottom' ? '底部' : preset.position === 'top' ? '顶部' : '居中'}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
