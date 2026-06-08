// src/features/tasks/VideoInfoCard.tsx
// 视频信息卡片 - 显示解析后的视频详细信息

import { useEffect, useState } from 'react'
import type { VideoParseResult } from '@/types'
import { videoApi } from '@/lib/api'
import { useTaskStore } from '@/stores/taskStore'
import { usePrefsStore } from '@/stores/prefsStore'

/** 视频信息卡片属性 */
interface VideoInfoCardProps {
  video: VideoParseResult
}

/**
 * 视频信息卡片
 * 显示视频标题、作者、时长、清晰度等信息
 * 提供下载按钮
 */
export function VideoInfoCard({ video }: VideoInfoCardProps) {
  const [isDownloading, setIsDownloading] = useState(false)
  const [isDownloadingCover, setIsDownloadingCover] = useState(false)
  const [lastCoverPath, setLastCoverPath] = useState('')
  const [coverDirDraft, setCoverDirDraft] = useState('')
  const [showCoverLocation, setShowCoverLocation] = useState(false)
  const { addTask, addLog } = useTaskStore()
  const coverDownloadDir = usePrefsStore((s) => s.preferences.cover_download_dir)
  const updatePrefs = usePrefsStore((s) => s.update)

  useEffect(() => {
    setCoverDirDraft(coverDownloadDir)
  }, [coverDownloadDir])

  /** 格式化时长 */
  const formatDuration = (seconds: number | null) => {
    if (!seconds) return '--:--'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  /** 处理下载按钮点击 */
  const handleDownload = async () => {
    if (isDownloading) return

    setIsDownloading(true)
    addLog('info', `开始下载: ${video.title}`)

    try {
      const result = await videoApi.download(video.id)
      addLog('info', result.message)

      // 添加任务到列表
      addTask({
        id: result.task_id,
        video_id: video.id,
        task_type: 'download',
        status: result.output_path ? 'completed' : 'pending',
        progress: result.output_path ? 100 : 0,
        output_path: result.output_path || null,
        error_message: null,
        created_at: new Date().toISOString(),
        completed_at: null,
      })
    } catch (error) {
      addLog('error', `下载失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsDownloading(false)
    }
  }

  /** 手动下载封面 */
  const handleDownloadCover = async () => {
    if (isDownloadingCover || !video.thumbnail_url) return

    setIsDownloadingCover(true)
    try {
      const result = await videoApi.downloadThumbnail(video.id, undefined, coverDownloadDir || undefined)
      setLastCoverPath(result.output_path)
      addLog('info', `封面已保存: ${result.output_path}`)
    } catch (error) {
      addLog('error', `封面下载失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setIsDownloadingCover(false)
    }
  }

  /** 选择封面保存目录，和一键自动保存封面共用同一个偏好 */
  const handleSelectCoverDirectory = async () => {
    const picker = window.electron?.dialog?.selectDirectory
    if (!picker) {
      addLog('warn', '当前环境不支持系统目录选择，请直接输入封面保存目录')
      return
    }
    try {
      const selected = await picker(coverDirDraft || coverDownloadDir)
      if (selected) setCoverDirDraft(selected)
    } catch (error) {
      addLog('error', `选择封面目录失败: ${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const handleSaveCoverDirectory = () => {
    updatePrefs({ cover_download_dir: coverDirDraft.trim() })
    addLog('info', coverDirDraft.trim() ? `封面保存目录已保存: ${coverDirDraft.trim()}` : '封面保存目录已恢复默认')
    setShowCoverLocation(false)
  }

  return (
    <div className="bg-background-elevated rounded-lg border border-border overflow-hidden">
      {/* 缩略图 */}
      {video.thumbnail_url && (
        <div className="aspect-video bg-background">
          <img
            src={video.thumbnail_url}
            alt={video.title || '视频缩略图'}
            className="w-full h-full object-cover"
          />
        </div>
      )}

      {/* 视频信息 */}
      <div className="p-4 space-y-3">
        {/* 标题 */}
        <h3 className="font-semibold text-lg leading-tight">
          {video.title || '未知标题'}
        </h3>

        {/* 作者和时长 */}
        <div className="flex items-center gap-4 text-sm text-foreground-muted">
          {video.author && (
            <span className="flex items-center gap-1">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
              {video.author}
            </span>
          )}
          <span className="flex items-center gap-1">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {formatDuration(video.duration)}
          </span>
        </div>

        {/* 可用清晰度 */}
        {video.formats.length > 0 && (
          <div>
            <p className="text-xs text-foreground-muted mb-1">可用清晰度</p>
            <div className="flex flex-wrap gap-1">
              {video.formats.slice(0, 5).map((format) => (
                <span
                  key={format.format_id}
                  className="px-2 py-0.5 text-xs bg-background rounded border border-border"
                >
                  {format.resolution}
                </span>
              ))}
              {video.formats.length > 5 && (
                <span className="px-2 py-0.5 text-xs text-foreground-muted">
                  +{video.formats.length - 5}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 可用字幕 */}
        {video.subtitles.length > 0 && (
          <div>
            <p className="text-xs text-foreground-muted mb-1">可用字幕</p>
            <div className="flex flex-wrap gap-1">
              {video.subtitles.slice(0, 5).map((sub, index) => (
                <span
                  key={index}
                  className="px-2 py-0.5 text-xs bg-background rounded border border-border"
                >
                  {sub.language}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 下载按钮 */}
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={handleDownload}
            disabled={isDownloading}
            className="h-10 rounded-md bg-primary font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isDownloading ? '下载中...' : '下载视频'}
          </button>
          <button
            onClick={handleDownloadCover}
            disabled={isDownloadingCover || !video.thumbnail_url}
            className="h-10 rounded-md border border-border bg-background font-medium transition-colors hover:bg-background/80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isDownloadingCover ? '保存中...' : '下载封面'}
          </button>
        </div>
        <div className="space-y-2 rounded-md border border-border p-2">
          <div className="flex items-center justify-between gap-2 text-xs text-foreground-muted">
            <span className="min-w-0 truncate">封面位置：{coverDownloadDir || '当前视频文件夹 / downloads'}</span>
            <button className="shrink-0 text-primary" onClick={() => setShowCoverLocation((value) => !value)}>
              {showCoverLocation ? '收起' : '更改'}
            </button>
          </div>
          {showCoverLocation && (
            <div className="flex flex-wrap gap-2">
              <input
                value={coverDirDraft}
                onChange={(event) => setCoverDirDraft(event.target.value)}
                placeholder="封面保存目录，留空使用默认"
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs"
              />
              <button className="h-9 rounded-md border border-border px-2 text-xs" onClick={handleSelectCoverDirectory}>选择</button>
              <button className="h-9 rounded-md bg-primary px-2 text-xs text-primary-foreground" onClick={handleSaveCoverDirectory}>保存</button>
            </div>
          )}
        </div>
        {lastCoverPath && (
          <p className="text-xs break-all text-foreground-muted">
            封面已保存到：{lastCoverPath}
          </p>
        )}
      </div>
    </div>
  )
}
