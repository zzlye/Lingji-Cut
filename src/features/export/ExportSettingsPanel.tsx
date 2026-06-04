// src/features/export/ExportSettingsPanel.tsx
// 最终导出设置 - 只控制合成导出阶段的格式、分辨率和码率
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { NumberField, SelectField, SwitchField, type FieldOption } from '@/components/fields'
import { usePrefsStore } from '@/stores/prefsStore'
import type { AutomationPreferences } from '@/types'

/** 最终导出格式选项 */
const OUTPUT_FORMAT_OPTIONS: FieldOption[] = [
  ['mp4', 'MP4'],
  ['mkv', 'MKV'],
  ['mov', 'MOV'],
  ['webm', 'WEBM'],
]

/** 最终导出分辨率选项 */
const RESOLUTION_OPTIONS: FieldOption[] = [
  ['original', '原始分辨率'],
  ['720p', '720p'],
  ['1080p', '1080p'],
  ['custom', '自定义'],
]

/** 最终导出面板 */
export function ExportSettingsPanel() {
  const preferences = usePrefsStore((state) => state.preferences)
  const updatePrefs = usePrefsStore((state) => state.update)
  const exportSettings = preferences.export_settings

  /** 更新最终导出子设置，避免覆盖其它字段 */
  const updateExportSettings = (updates: Partial<AutomationPreferences['export_settings']>) => {
    updatePrefs({
      export_settings: {
        ...exportSettings,
        ...updates,
      },
    })
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div>
        <h2 className="text-base font-semibold">最终导出</h2>
        <p className="text-sm text-muted-foreground">这里只控制最后“合成导出”那一步，不影响画面处理阶段的差异化参数。</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">导出基础</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <SwitchField
            label="最终按导出设置统一输出"
            description="开启后，字幕和配音完成后会再按这里的分辨率与码率收口导出；关闭后只按导出格式直接输出。"
            checked={preferences.export_with_settings}
            onChange={(value) => updatePrefs({ export_with_settings: value })}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label="导出格式"
              value={preferences.output_format}
              options={OUTPUT_FORMAT_OPTIONS}
              onChange={(value) => updatePrefs({ output_format: value as AutomationPreferences['output_format'] })}
              description="最终成品文件格式。"
            />
            <SelectField
              label="导出分辨率"
              value={exportSettings.resolution}
              options={RESOLUTION_OPTIONS}
              onChange={(value) => updateExportSettings({ resolution: value as AutomationPreferences['export_settings']['resolution'] })}
              description="只在最终导出阶段生效。"
            />
            {exportSettings.resolution === 'custom' && (
              <>
                <NumberField
                  label="导出宽度"
                  value={exportSettings.width}
                  min={320}
                  step={2}
                  suffix="px"
                  onChange={(value) => updateExportSettings({ width: value })}
                />
                <NumberField
                  label="导出高度"
                  value={exportSettings.height}
                  min={180}
                  step={2}
                  suffix="px"
                  onChange={(value) => updateExportSettings({ height: value })}
                />
              </>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">码率控制</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <SwitchField
            label="启用最终导出码率"
            description="用于控制成品体积；不改动画面处理阶段的码率策略。"
            checked={exportSettings.bitrate_enabled}
            onChange={(value) => updateExportSettings({ bitrate_enabled: value })}
          />
          {exportSettings.bitrate_enabled && (
            <NumberField
              label="目标码率"
              value={exportSettings.bitrate_kbps}
              min={200}
              max={20000}
              step={100}
              suffix="kb/s"
              description="数值越高，通常越清晰，文件也会更大。"
              onChange={(value) => updateExportSettings({ bitrate_kbps: value })}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
