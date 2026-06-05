# backend/api/videos.py
# 视频 API 路由 - 提供视频解析和下载接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json
import os

from pydantic import BaseModel
from typing import Optional

from ..core import DedupChecker, Downloader
from ..core.paths import ensure_video_workspace
from ..core.process_control import TaskControlRequested
from ..models import get_db, VideoSource, DownloadTask

# 创建路由器
router = APIRouter(prefix="/videos", tags=["videos"])


class ParseRequest(BaseModel):
    """视频解析请求"""
    url: str


class ParseResponse(BaseModel):
    """视频解析响应"""
    id: int
    video_id: str
    platform: str
    title: Optional[str] = None
    author: Optional[str] = None
    duration: Optional[int] = None
    thumbnail_url: Optional[str] = None
    formats: list[dict] = []
    subtitles: list[dict] = []


class DownloadRequest(BaseModel):
    """视频下载请求"""
    video_id: int
    format_id: Optional[str] = None
    output_format: str = "mp4"


class ThumbnailDownloadRequest(BaseModel):
    """手动下载封面请求"""
    video_id: int
    file_name: Optional[str] = None


class ThumbnailDownloadResponse(BaseModel):
    """手动下载封面响应"""
    message: str
    output_path: str


@router.post("/parse", response_model=ParseResponse)
async def parse_video(request: ParseRequest, db: Session = Depends(get_db)):
    """
    解析 YouTube 视频信息
    调用 yt-dlp 获取视频元数据（标题、作者、时长、清晰度、字幕等）
    """
    downloader = Downloader()
    dedup = DedupChecker(db)

    try:
        video_info = downloader.parse_video(request.url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    existing = dedup.check_by_video_id(video_info["platform"], video_info["video_id"])
    if existing:
        # 已解析过的视频仍返回最新请求结果，避免用户看不到格式和字幕列表。
        existing.url = request.url
        existing.title = video_info.get("title")
        existing.author = video_info.get("author")
        existing.duration = video_info.get("duration")
        existing.thumbnail_url = video_info.get("thumbnail_url")
        existing.formats = json.dumps(video_info.get("formats", []), ensure_ascii=False)
        existing.subtitles = json.dumps(video_info.get("subtitles", []), ensure_ascii=False)
        db.commit()
        db.refresh(existing)
        video_source = existing
    else:
        video_source = dedup.add_video_source(video_info)

    return ParseResponse(
        id=video_source.id,
        video_id=video_info["video_id"],
        platform=video_info["platform"],
        title=video_info.get("title"),
        author=video_info.get("author"),
        duration=video_info.get("duration"),
        thumbnail_url=video_info.get("thumbnail_url"),
        formats=video_info.get("formats", []),
        subtitles=video_info.get("subtitles", []),
    )


def _task_control_key(task_id: int) -> str:
    """生成底层任务控制 key"""
    return f"task:{task_id}"


def _mark_task_controlled(task: DownloadTask, exc: TaskControlRequested) -> None:
    """根据用户控制动作更新下载任务状态"""
    task.status = "paused" if exc.action == "pause" else "cancelled"
    task.error_message = "用户暂停，等待继续" if exc.action == "pause" else "用户取消"


@router.post("/download")
def download_video(request: DownloadRequest, db: Session = Depends(get_db)):
    """
    创建视频下载任务
    将下载任务添加到任务队列
    """
    video = db.query(VideoSource).filter(VideoSource.id == request.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频记录不存在，请先解析视频")

    task = DownloadTask(
        video_id=video.id,
        task_type="download",
        status="downloading",
        progress=0,
        params=json.dumps({
            "format_id": request.format_id,
            "output_format": request.output_format,
        }, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    downloader = Downloader()

    def on_progress(progress: float, _: str) -> None:
        """同步更新下载进度"""
        task.progress = progress
        db.commit()

    try:
        workspace_paths = ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
        output_path = downloader.download_video(
            url=video.url,
            output_dir=workspace_paths["downloads_dir"],
            format_id=request.format_id,
            output_format=request.output_format,
            progress_callback=on_progress,
            control_keys=[_task_control_key(task.id)],
        )
        task.status = "completed"
        task.progress = 100
        task.output_path = output_path
        db.commit()
        return {"message": "下载完成", "task_id": task.id, "output_path": output_path}
    except TaskControlRequested as exc:
        _mark_task_controlled(task, exc)
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/download-thumbnail", response_model=ThumbnailDownloadResponse)
def download_thumbnail(request: ThumbnailDownloadRequest, db: Session = Depends(get_db)):
    """按当前解析视频信息把封面下载到该视频的独立工作目录"""
    video = db.query(VideoSource).filter(VideoSource.id == request.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频记录不存在，请先解析视频")
    if not video.thumbnail_url:
        raise HTTPException(status_code=400, detail="当前视频没有可下载的封面地址")

    workspace_paths = ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
    raw_file_name = str(request.file_name or "").strip()
    cover_name = raw_file_name or f"{_safe_cover_base_name(video.title or video.video_id or 'thumbnail')}_cover"

    try:
        output_path = Downloader().download_thumbnail(
            thumbnail_url=video.thumbnail_url,
            output_dir=workspace_paths["downloads_dir"],
            file_name=cover_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ThumbnailDownloadResponse(
        message="封面下载完成",
        output_path=output_path,
    )


def _safe_cover_base_name(value: str) -> str:
    """把视频标题转换成封面文件名片段，避免非法字符导致保存失败"""
    safe_name = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in str(value or "").strip())
    safe_name = safe_name.strip("._") or "thumbnail"
    return os.path.splitext(safe_name)[0]
