# backend/api/settings.py
# 设置 API 路由 - 提供项目文件夹保存和 videos 目录创建

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.paths import (
    get_project_paths,
    load_ytdlp_cookie_settings,
    reset_project_root,
    save_project_root,
    save_ytdlp_cookie_settings,
)
from ..core.tooling import ToolStatus, check_required_tools


router = APIRouter(prefix="/settings", tags=["settings"])


class ProjectPathUpdate(BaseModel):
    """项目目录更新请求"""
    project_root: str


class YtdlpCookieSettingsUpdate(BaseModel):
    """YouTube cookies 配置更新请求"""
    cookies_file: str | None = None
    cookies_browser: str | None = None


class YtdlpCookieSettingsResponse(BaseModel):
    """YouTube cookies 配置响应"""
    cookies_file: str
    cookies_browser: str
    cookies_file_exists: bool


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


def _cookie_settings_to_response(settings: dict) -> YtdlpCookieSettingsResponse:
    """补充 cookies 文件存在状态，避免前端重复判断 Windows 路径"""
    cookies_file = str(settings.get("cookies_file") or "").strip()
    return YtdlpCookieSettingsResponse(
        cookies_file=cookies_file,
        cookies_browser=str(settings.get("cookies_browser") or "").strip(),
        cookies_file_exists=bool(cookies_file and os.path.isfile(cookies_file)),
    )


@router.get("/paths")
async def get_paths():
    """获取项目目录，并确保 videos 文件夹自动创建"""
    return get_project_paths(create=True)


@router.get("/tools", response_model=dict[str, ToolStatusResponse])
async def get_tools():
    """获取 yt-dlp 和 ffmpeg 工具状态"""
    return {
        key: _tool_status_to_response(status)
        for key, status in check_required_tools().items()
    }


@router.get("/ytdlp-cookies", response_model=YtdlpCookieSettingsResponse)
async def get_ytdlp_cookies():
    """获取 YouTube 登录 cookies 配置"""
    return _cookie_settings_to_response(load_ytdlp_cookie_settings())


@router.put("/ytdlp-cookies", response_model=YtdlpCookieSettingsResponse)
async def update_ytdlp_cookies(request: YtdlpCookieSettingsUpdate):
    """保存 YouTube 登录 cookies 配置"""
    try:
        settings = save_ytdlp_cookie_settings(request.cookies_file, request.cookies_browser)
        return _cookie_settings_to_response(settings)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cookies 配置保存失败: {exc}") from exc


@router.put("/paths")
async def update_paths(request: ProjectPathUpdate):
    """保存项目目录，并自动创建 videos 文件夹"""
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
