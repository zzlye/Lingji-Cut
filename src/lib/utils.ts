// src/lib/utils.ts
// shadcn/ui 标准工具：合并 Tailwind 类名，自动处理条件类名与冲突类名
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** 合并并去重 Tailwind 类名 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
