# backend/api/settings.py
# 设置 API 路由 - 提供项目文件夹保存和子目录创建

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.paths import get_project_paths, reset_project_root, save_project_root
from ..core.tooling import ToolStatus, check_required_tools


router = APIRouter(prefix="/settings", tags=["settings"])


class ProjectPathUpdate(BaseModel):
    """项目目录更新请求"""
    project_root: str


class ToolStatusResponse(BaseModel):
    """外部工具状态响应"""
    name: str
    command: str
    available: bool
    version: str | None = None
    source: str
    error_message: str | None = None


def _tool_status_to_response(status: ToolStatus) -> ToolStatusResponse:
    """转换工具检测结果"""
    return ToolStatusResponse(
        name=status.name,
        command=status.command,
        available=status.available,
        version=status.version,
        source=status.source,
        error_message=status.error_message,
    )

@router.get("/paths")
async def get_paths():
    """获取项目目录，并确保常用子文件夹自动创建"""
    return get_project_paths(create=True)


@router.get("/tools", response_model=dict[str, ToolStatusResponse])
async def get_tools():
    """获取 yt-dlp 和 ffmpeg 工具状态"""
    return {
        key: _tool_status_to_response(status)
        for key, status in check_required_tools().items()
    }


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
