// src/features/download/VideoPreview.tsx
// 右侧视频预览面板 - 显示当前视频预览和字幕预览

import { useTaskStore } from '@/stores/taskStore'

/**
 * 视频预览面板
 * 显示当前处理的视频预览、字幕预览、样式配置
 */
export function VideoPreview() {
  const { currentVideo } = useTaskStore()

  return (
    <div className="h-full flex flex-col">
      {/* 面板标题 */}
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-medium">预览</h3>
      </div>

      {/* 预览内容 */}
      <div className="flex-1 overflow-auto">
        {currentVideo ? (
          <div className="p-4 space-y-4">
            {/* 视频预览区域 */}
            <div className="aspect-video bg-background rounded-lg border border-border flex items-center justify-center">
              {currentVideo.thumbnail_url ? (
                <img
                  src={currentVideo.thumbnail_url}
                  alt="视频预览"
                  className="w-full h-full object-cover rounded-lg"
                />
              ) : (
                <div className="text-center text-foreground-muted">
                  <svg className="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p className="text-xs">视频预览</p>
                </div>
              )}
            </div>

            {/* 字幕预览 */}
            <div>
              <h4 className="text-xs font-medium text-foreground-muted mb-2">字幕预览</h4>
              <div className="bg-background rounded-lg border border-border p-3 min-h-[100px]">
                <div className="text-center">
                  <p className="text-sm">这是一行示例字幕</p>
                  <p className="text-sm">This is a sample subtitle</p>
                </div>
              </div>
            </div>

            {/* 样式配置 */}
            <div>
              <h4 className="text-xs font-medium text-foreground-muted mb-2">字幕样式</h4>
              <div className="space-y-2">
                <StyleItem label="字体" value="Microsoft YaHei" />
                <StyleItem label="字号" value="48" />
                <StyleItem label="颜色" value="#FFFFFF" isColor />
                <StyleItem label="描边" value="2px" />
                <StyleItem label="位置" value="底部居中" />
              </div>
            </div>
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-foreground-muted">
            <div className="text-center">
              <svg className="w-16 h-16 mx-auto mb-3 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <p className="text-sm">解析视频后</p>
              <p className="text-xs mt-1">在此预览字幕效果</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** 样式配置项 */
function StyleItem({ label, value, isColor = false }: { label: string; value: string; isColor?: boolean }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-foreground-muted">{label}</span>
      <div className="flex items-center gap-1.5">
        {isColor && (
          <span
            className="w-3 h-3 rounded-sm border border-border"
            style={{ backgroundColor: value }}
          />
        )}
        <span>{value}</span>
      </div>
    </div>
  )
}
