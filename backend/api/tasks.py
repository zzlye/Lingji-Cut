# backend/api/tasks.py
# 任务 API 路由 - 提供任务管理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import not_
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from ..core.process_control import clear_control_request
from ..core.task_runtime import request_task_control, terminate_task_external_processes
from ..models import get_db, DownloadTask

# 创建路由器
router = APIRouter(prefix="/tasks", tags=["tasks"])

RUNNING_STATUSES = {"processing", "downloading"}
ACTIVE_STATUSES = {"pending", "processing", "downloading", "paused"}
RETRYABLE_STATUSES = {"failed", "cancelled", "paused"}


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
    can_pause: bool = False
    can_cancel: bool = False
    can_retry: bool = False
    can_delete: bool = False

    class Config:
        from_attributes = True


def _task_to_response(task: DownloadTask) -> TaskResponse:
    """补充任务可执行操作，统一给前端展示"""
    return TaskResponse(
        id=task.id,
        video_id=task.video_id,
        task_type=task.task_type,
        status=task.status,
        progress=task.progress or 0,
        output_path=task.output_path,
        error_message=task.error_message,
        created_at=task.created_at,
        completed_at=task.completed_at,
        can_pause=task.status in {"pending", "processing", "downloading"},
        can_cancel=task.status in ACTIVE_STATUSES,
        can_retry=task.status in RETRYABLE_STATUSES and bool(task.parent_job_id),
        can_delete=task.status not in RUNNING_STATUSES,
    )


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
    return [_task_to_response(task) for task in query.order_by(DownloadTask.created_at.desc()).all()]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: int, db: Session = Depends(get_db)):
    """获取单个任务详情"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_to_response(task)


@router.post("/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    """重试失败的任务"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in RETRYABLE_STATUSES:
        raise HTTPException(status_code=400, detail="只能重试失败、取消或暂停的任务")

    if task.parent_job_id:
        from .automation import _prepare_job_for_resume, _submit_automation_job
        from ..models import AutomationJobRecord

        clear_control_request(f"task:{task_id}")
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == task.parent_job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="所属自动化任务不存在")
        _prepare_job_for_resume(job)
        task.status = "pending"
        task.error_message = None
        task.progress = 0
        db.commit()
        _submit_automation_job(job.id, True)
        return {"message": "所属自动化任务已从断点继续", "task_id": task_id, "job_id": job.id}

    clear_control_request(f"task:{task_id}")
    task.status = "pending"
    task.error_message = None
    task.progress = 0
    db.commit()
    return {"message": "任务已重置", "task_id": task_id}


def mark_interrupted_tasks(db: Session) -> int:
    """把执行中但已失去进程上下文的底层任务标记为失败"""
    tasks = db.query(DownloadTask).filter(DownloadTask.status.in_(RUNNING_STATUSES)).all()
    now = datetime.now()
    for task in tasks:
        terminate_task_external_processes(db, task)
        task.status = "failed"
        task.progress = max(0, min(float(task.progress or 0), 99))
        task.error_message = task.error_message or "任务已中断或长时间无响应，已标记为失败，可删除后重新执行"
        task.completed_at = now
    db.commit()
    return len(tasks)


@router.post("/{task_id}/pause")
async def pause_task(task_id: int, db: Session = Depends(get_db)):
    """暂停底层任务，并终止当前外部进程"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in {"processing", "downloading", "pending"}:
        raise HTTPException(status_code=400, detail="只有等待中、下载中或处理中的任务可以暂停")
    killed_count = request_task_control(db, task, "pause")
    task.status = "paused"
    task.error_message = "用户暂停，等待重试或继续"
    db.commit()
    return {"message": "任务已暂停", "task_id": task_id, "killed_count": killed_count}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """取消底层任务，并终止当前外部进程"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail="任务已经结束，不能取消")
    killed_count = request_task_control(db, task, "cancel")
    task.status = "cancelled"
    task.error_message = "用户取消"
    task.completed_at = datetime.now()
    db.commit()
    return {"message": "任务已取消", "task_id": task_id, "killed_count": killed_count}


@router.delete("/{task_id}")
async def delete_task(task_id: int, force: bool = False, db: Session = Depends(get_db)):
    """删除单条底层任务记录"""
    task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in RUNNING_STATUSES and not force:
        raise HTTPException(status_code=400, detail="任务仍在执行中，请先暂停或取消后再删除")
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
