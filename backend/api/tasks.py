# backend/api/tasks.py
# 任务 API 路由 - 提供任务管理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import not_
from datetime import datetime
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
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("", response_model=List[TaskResponse])
async def get_tasks(
    status: Optional[str] = None,
    include_orphans: bool = False,
    db: Session = Depends(get_db)
):
    """
    获取任务列表
    支持按状态筛选（pending/downloading/processing/completed/failed）
    """
    query = db.query(DownloadTask)
    if not include_orphans:
        # 过滤旧版本或手动调试留下的孤儿失败记录，避免用户打开任务列表就看到无关错误。
        query = query.filter(not_(
            (DownloadTask.video_id <= 0) &
            (DownloadTask.parent_job_id.is_(None)) &
            (DownloadTask.status == "failed")
        ))
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


RUNNING_STATUSES = {"processing", "downloading"}


def mark_interrupted_tasks(db: Session) -> int:
    """把执行中但已失去进程上下文的底层任务标记为失败"""
    tasks = db.query(DownloadTask).filter(DownloadTask.status.in_(RUNNING_STATUSES)).all()
    now = datetime.now()
    for task in tasks:
        task.status = "failed"
        task.progress = max(0, min(float(task.progress or 0), 99))
        task.error_message = task.error_message or "任务已中断或长时间无响应，已标记为失败，可删除后重新执行"
        task.completed_at = now
    db.commit()
    return len(tasks)


@router.delete("/{task_id}")
async def delete_task(task_id: int, force: bool = False, db: Session = Depends(get_db)):
    """删除单条底层任务记录"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in RUNNING_STATUSES and not force:
        raise HTTPException(status_code=400, detail="任务仍显示执行中，如确认已卡住请使用强制清理")
    db.delete(task)
    db.commit()
    return {"message": "任务记录已删除", "task_id": task_id}


@router.delete("")
async def clear_tasks(
    status: Optional[str] = None,
    include_running: bool = False,
    db: Session = Depends(get_db),
):
    """批量清理底层任务记录，默认只清理已结束任务"""
    query = db.query(DownloadTask)
    if status:
        query = query.filter(DownloadTask.status == status)
    if not include_running:
        query = query.filter(DownloadTask.status.notin_(["processing", "downloading"]))
    tasks = query.all()
    deleted_count = len(tasks)
    for task in tasks:
        db.delete(task)
    db.commit()
    return {"message": "任务记录已清理", "deleted_count": deleted_count}


@router.post("/cleanup-interrupted")
async def cleanup_interrupted_tasks(db: Session = Depends(get_db)):
    """把重启或异常中断后遗留的执行中任务标记为失败，方便用户删除或重试"""
    return {"message": "已清理中断任务", "updated_count": mark_interrupted_tasks(db)}
