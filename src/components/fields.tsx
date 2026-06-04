// src/components/fields.tsx
// 统一设置字段组件 - 基于 shadcn 封装，替换各设置面板各自实现的原生 input/select/checkbox 伪组件
import type { ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import type { RandomRange } from '@/types'

/** 选项类型：[值, 显示文案] */
export type FieldOption = [string, string]

/** Radix Select 不允许空字符串选项值，这里用占位值在组件内部转换 */
const EMPTY_SELECT_SENTINEL = '__EMPTY_SELECT_OPTION__'

/** 字段外壳：标签 + 可选说明 + 控件，纵向排布 */
export function Field({ label, description, children, className }: { label: ReactNode; description?: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <Label className="text-sm font-normal">{label}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      {children}
    </div>
  )
}

/** 文本输入（支持密码） */
export function TextField({ label, value, onChange, placeholder, description, type = 'text', className }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; description?: string; type?: string; className?: string
}) {
  return (
    <Field label={label} description={description} className={className}>
      <Input type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </Field>
  )
}

/** 数字输入 */
export function NumberField({ label, value, onChange, min, max, step, description, suffix }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; description?: string; suffix?: string
}) {
  return (
    <Field label={label} description={description}>
      <div className="flex items-center gap-2">
        <Input type="number" value={value} min={min} max={max} step={step} onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span className="shrink-0 text-xs text-muted-foreground">{suffix}</span>}
      </div>
    </Field>
  )
}

/** 下拉选择 */
export function SelectField({ label, value, options, onChange, description, placeholder }: {
  label: string; value: string; options: FieldOption[]; onChange: (v: string) => void; description?: string; placeholder?: string
}) {
  const hasEmptyOption = options.some(([optionValue]) => optionValue === '')
  const normalizedOptions = options.map(([optionValue, optionLabel]) => [optionValue === '' ? EMPTY_SELECT_SENTINEL : optionValue, optionLabel] as FieldOption)
  const normalizedValue = value === '' ? (hasEmptyOption ? EMPTY_SELECT_SENTINEL : undefined) : value

  return (
    <Field label={label} description={description}>
      <Select value={normalizedValue} onValueChange={(nextValue) => onChange(nextValue === EMPTY_SELECT_SENTINEL ? '' : nextValue)}>
        <SelectTrigger className="w-full"><SelectValue placeholder={placeholder} /></SelectTrigger>
        <SelectContent>
          {normalizedOptions.map(([optionValue, optionLabel]) => <SelectItem key={optionValue} value={optionValue}>{optionLabel}</SelectItem>)}
        </SelectContent>
      </Select>
    </Field>
  )
}

/** 多段切换（分段按钮） */
export function SegmentedField({ label, value, options, onChange, description }: {
  label?: string; value: string; options: FieldOption[]; onChange: (v: string) => void; description?: string
}) {
  const seg = (
    <div className="inline-flex rounded-lg bg-muted p-0.5">
      {options.map(([v, l]) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className={cn('rounded-md px-3 py-1 text-sm transition-colors', value === v ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
        >
          {l}
        </button>
      ))}
    </div>
  )
  return label ? <Field label={label} description={description}>{seg}</Field> : seg
}

/** 开关行：标签 + 说明 + Switch（带边框卡片） */
export function SwitchField({ label, description, checked, onChange }: {
  label: string; description?: string; checked: boolean; onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
      <div className="min-w-0">
        <p className="text-sm">{label}</p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  )
}

/** 滑块：标签 + 当前值 + Slider */
export function SliderField({ label, value, min, max, step = 1, onChange, description, suffix, format }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; description?: string; suffix?: string; format?: (v: number) => string
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-normal">{label}</Label>
        <span className="text-xs tabular-nums text-muted-foreground">{format ? format(value) : value}{suffix}</span>
      </div>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <Slider value={[value]} min={min} max={max} step={step} onValueChange={([v]) => onChange(v)} />
    </div>
  )
}

/** 颜色选择：取色器 + 十六进制 + 预览 */
export function ColorField({ label, value, onChange, description, disabled }: {
  label: string; value: string; onChange: (v: string) => void; description?: string; disabled?: boolean
}) {
  return (
    <Field label={label} description={description}>
      <div className={cn('flex items-center gap-2', disabled && 'opacity-50')}>
        <input
          type="color"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className="size-9 shrink-0 cursor-pointer rounded-md border border-input bg-transparent disabled:cursor-not-allowed"
          aria-label={label}
        />
        <Input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="font-mono uppercase" />
      </div>
    </Field>
  )
}

/** 多行文本 */
export function TextareaField({ label, value, onChange, placeholder, description, rows = 4 }: {
  label?: string; value: string; onChange: (v: string) => void; placeholder?: string; description?: string; rows?: number
}) {
  const area = <Textarea value={value} placeholder={placeholder} rows={rows} onChange={(e) => onChange(e.target.value)} className="resize-y" />
  return label ? <Field label={label} description={description}>{area}</Field> : area
}

/**
 * 随机范围字段（重做）：取代旧的「启用+最小+最大+固定值+随机」5 控件混乱。
 * 启用开关 +「固定值 / 随机范围」切换：固定值用单滑块，随机范围用双滑块。
 */
export function RangeField({ label, value, onChange, min = 0, max = 2, step = 0.01, description, suffix, decimals = 2 }: {
  label: string
  value: RandomRange
  onChange: (updates: Partial<RandomRange>) => void
  min?: number
  max?: number
  step?: number
  description?: string
  suffix?: string
  decimals?: number
}) {
  const fmt = (n: number) => Number(n).toFixed(decimals)
  const fixedValue = value.value ?? value.min
  return (
    <div className={cn('space-y-2.5 rounded-lg border p-3 transition-opacity', !value.enabled && 'opacity-60')}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">{label}</p>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        <Switch checked={value.enabled} onCheckedChange={(v) => onChange({ enabled: v })} />
      </div>

      {value.enabled && (
        <Tabs value={value.random ? 'random' : 'fixed'} onValueChange={(m) => onChange({ random: m === 'random' })}>
          <TabsList className="w-full">
            <TabsTrigger value="fixed" className="flex-1">固定值</TabsTrigger>
            <TabsTrigger value="random" className="flex-1">随机范围</TabsTrigger>
          </TabsList>
          <TabsContent value="fixed" className="space-y-1.5 pt-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">数值</span>
              <span className="text-xs tabular-nums">{fmt(fixedValue)}{suffix}</span>
            </div>
            <Slider value={[fixedValue]} min={min} max={max} step={step} onValueChange={([v]) => onChange({ value: v, random: false })} />
          </TabsContent>
          <TabsContent value="random" className="space-y-1.5 pt-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">范围</span>
              <span className="text-xs tabular-nums">{fmt(value.min)} ~ {fmt(value.max)}{suffix}</span>
            </div>
            <Slider
              value={[value.min, value.max]}
              min={min}
              max={max}
              step={step}
              onValueChange={([lo, hi]) => onChange({ min: lo, max: hi, random: true })}
            />
            <p className="text-[11px] text-muted-foreground">每个视频在此范围内随机取值，用于差异化处理</p>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

/** 九宫格位置选择 */
export function PositionGrid({ value, options, onChange }: {
  value: string; options: FieldOption[]; onChange: (v: string) => void
}) {
  return (
    <div className="grid w-full max-w-xs grid-cols-3 gap-1.5">
      {options.map(([v, l]) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className={cn(
            'h-10 rounded-md border text-xs transition-colors',
            value === v ? 'border-primary bg-primary/15 font-medium text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
          )}
        >
          {l}
        </button>
      ))}
    </div>
  )
}
