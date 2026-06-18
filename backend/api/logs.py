# backend/api/logs.py
# 活动日志接口 - 给前端读取后端最近日志，避免只在 CMD 控制台可见

from fastapi import APIRouter
from pydantic import BaseModel

from ..utils import get_recent_logs

router = APIRouter(prefix="/logs", tags=["logs"])


class BackendLogResponse(BaseModel):
    id: int
    timestamp: str
    level: str
    source: str
    message: str


@router.get("", response_model=list[BackendLogResponse])
def list_logs():
    """读取最近 200 条后端活动日志"""
    return get_recent_logs()
