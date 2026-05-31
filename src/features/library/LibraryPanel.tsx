// src/features/library/LibraryPanel.tsx
// 素材库面板 - 显示已下载的视频素材

/**
 * 素材库面板
 * 显示已下载的视频文件列表
 */
export function LibraryPanel() {
  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-medium">素材库</h3>
      </div>

      <div className="flex-1 overflow-auto p-4">
        <div className="flex flex-col items-center justify-center py-12 text-foreground-muted">
          <svg className="w-12 h-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          <p className="text-sm">暂无素材</p>
          <p className="text-xs mt-1">下载的视频将显示在这里</p>
        </div>
      </div>
    </div>
  )
}
