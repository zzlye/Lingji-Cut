# backend/api/settings.py
# 设置 API 路由 - 提供项目文件夹保存和子目录创建

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.paths import get_project_paths, reset_project_root, save_project_root


router = APIRouter(prefix="/settings", tags=["settings"])


class ProjectPathUpdate(BaseModel):
    """项目目录更新请求"""
    project_root: str

@router.get("/paths")
async def get_paths():
    """获取项目目录，并确保常用子文件夹自动创建"""
    return get_project_paths(create=True)


@router.put("/paths")
async def update_paths(request: ProjectPathUpdate):
    """保存项目目录，并自动创建业务子文件夹"""
    try:
        save_project_root(request.project_root)
        return get_project_paths(create=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"项目目录不可用: {exc}") from exc


@router.post("/paths/reset")
async def reset_paths():
    """恢复默认项目目录"""
    try:
        reset_project_root()
        return get_project_paths(create=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"恢复默认目录失败: {exc}") from exc
