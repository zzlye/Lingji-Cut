# backend/api/exports.py
# 导出 API 路由 - 提供视频导出接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

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


@router.post("/create")
async def create_export(request: ExportRequest, db: Session = Depends(get_db)):
    """创建导出任务"""
    # TODO: 创建导出任务
    return {"message": "导出任务已创建", "task_id": 1}
