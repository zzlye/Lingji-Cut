# backend/api/tasks.py
# 任务 API 路由 - 提供任务管理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from ..models import get_db, DownloadTask

# 创建路由器
router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    video_id: int
    task_type: str
    status: str
    progress: float
    output_path: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("", response_model=List[TaskResponse])
async def get_tasks(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    获取任务列表
    支持按状态筛选（pending/downloading/processing/completed/failed）
    """
    query = db.query(DownloadTask)
    if status:
        query = query.filter(DownloadTask.status == status)
    return query.order_by(DownloadTask.created_at.desc()).all()


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: int, db: Session = Depends(get_db)):
    """获取单个任务详情"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    """重试失败的任务"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "failed":
        raise HTTPException(status_code=400, detail="只能重试失败的任务")

    task.status = "pending"
    task.error_message = None
    task.progress = 0
    db.commit()
    return {"message": "任务已重置", "task_id": task_id}
