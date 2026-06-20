# backend/api/exports.py
# 导出 API 路由 - 提供视频导出接口

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..core import FFmpegProcessor, SubtitleEngine
from ..core.process_control import TaskControlRequested
from ..models import get_db, DownloadTask
from .subtitles import _parse_subtitle_entries

# 创建路由器
router = APIRouter(prefix="/exports", tags=["exports"])


class ExportRequest(BaseModel):
    """导出请求"""
    video_path: str
    subtitle_path: Optional[str] = None
    audio_path: Optional[str] = None
    output_format: str = "mp4"
    resolution: Optional[str] = None
    bitrate: Optional[str] = None
    audio_mode: str = "replace"
    original_volume: float = 0.25


class ExportResponse(BaseModel):
    """导出响应"""
    message: str
    task_id: int
    output_path: str


def _clean_export_subtitle_path(subtitle_path: str) -> str:
    """生成导出前清理字幕的临时 ASS 路径"""
    base_dir = os.path.dirname(os.path.abspath(subtitle_path)) or os.getcwd()
    base_name = os.path.splitext(os.path.basename(subtitle_path))[0]
    return os.path.join(base_dir, f"{base_name}_export_clean.ass")


def _prepare_subtitle_for_export_burn(subtitle_path: str, video_path: str, processor: FFmpegProcessor) -> str:
    """导出烧录前统一清理逗号、句号、省略号，避免旧字幕文件绕过过滤"""
    engine = SubtitleEngine()
    output_path = _clean_export_subtitle_path(subtitle_path)
    entries = _parse_subtitle_entries(engine, subtitle_path)
    if not entries:
        raise RuntimeError("字幕文件为空或无法解析，不能导出")
    display_entries = engine.normalize_entries_for_display(entries, {})
    video_size = processor.media_video_size(video_path)
    engine.generate_ass(display_entries, output_path, {}, video_size=video_size)
    return output_path


@router.post("/create", response_model=ExportResponse)
def create_export(request: ExportRequest, db: Session = Depends(get_db)):
    """创建并执行导出任务"""
    if not request.video_path or not request.video_path.strip():
        raise HTTPException(status_code=400, detail="请先填写输入视频路径")
    if not os.path.exists(request.video_path):
        raise HTTPException(status_code=404, detail=f"输入文件不存在: {request.video_path}")
    if request.subtitle_path and not os.path.exists(request.subtitle_path):
        raise HTTPException(status_code=404, detail=f"字幕文件不存在: {request.subtitle_path}")
    if request.audio_path and not os.path.exists(request.audio_path):
        raise HTTPException(status_code=404, detail=f"音频文件不存在: {request.audio_path}")

    task = DownloadTask(
        video_id=0,
        task_type="export",
        status="processing",
        progress=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    processor = FFmpegProcessor()
    try:
        working_video = request.video_path

        def on_progress(progress: float, start: float, weight: float) -> None:
            """同步导出任务进度"""
            task.progress = max(0.0, min(99.0, start + progress * weight))
            db.commit()

        # 先烧录字幕，再按需合成配音，最后复制/转换到导出目录。
        if request.subtitle_path:
            subtitle_for_burn = _prepare_subtitle_for_export_burn(request.subtitle_path, working_video, processor)
            working_video = processor.burn_subtitles(
                video_path=working_video,
                subtitle_path=subtitle_for_burn,
                control_keys=[f"task:{task.id}"],
                progress_callback=lambda progress: on_progress(progress, 0, 0.35),
            )
            task.progress = 35
            db.commit()

        if request.audio_path:
            working_video = processor.merge_audio_video(
                video_path=working_video,
                audio_path=request.audio_path,
                mode=request.audio_mode,
                volume_ratio=request.original_volume,
                control_keys=[f"task:{task.id}"],
                progress_callback=lambda progress: on_progress(progress, 35, 0.35),
            )
            task.progress = 70
            db.commit()

        output_path = processor.convert_format(
            input_path=working_video,
            output_format=request.output_format,
            control_keys=[f"task:{task.id}"],
            progress_callback=lambda progress: on_progress(progress, 70, 0.29),
        )

        task.status = "completed"
        task.progress = 100
        task.output_path = output_path
        db.commit()
        return ExportResponse(message="导出完成", task_id=task.id, output_path=output_path)
    except TaskControlRequested as exc:
        task.status = "paused" if exc.action == "pause" else "cancelled"
        task.error_message = "用户暂停，等待继续" if exc.action == "pause" else "用户取消"
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
