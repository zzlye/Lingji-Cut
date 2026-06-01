# backend/api/exports.py
# 导出 API 路由 - 提供视频导出接口

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..core import FFmpegProcessor
from ..models import get_db, DownloadTask

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


@router.post("/create", response_model=ExportResponse)
async def create_export(request: ExportRequest, db: Session = Depends(get_db)):
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

        # 先烧录字幕，再按需合成配音，最后复制/转换到导出目录。
        if request.subtitle_path:
            working_video = processor.burn_subtitles(
                video_path=working_video,
                subtitle_path=request.subtitle_path,
            )
            task.progress = 35
            db.commit()

        if request.audio_path:
            working_video = processor.merge_audio_video(
                video_path=working_video,
                audio_path=request.audio_path,
                mode=request.audio_mode,
                volume_ratio=request.original_volume,
            )
            task.progress = 70
            db.commit()

        output_path = processor.convert_format(
            input_path=working_video,
            output_format=request.output_format,
        )

        task.status = "completed"
        task.progress = 100
        task.output_path = output_path
        db.commit()
        return ExportResponse(message="导出完成", task_id=task.id, output_path=output_path)
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
