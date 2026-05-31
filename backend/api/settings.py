# backend/api/settings.py
# 设置 API 路由 - 提供项目文件夹和工具路径信息

import os

from fastapi import APIRouter


router = APIRouter(prefix="/settings", tags=["settings"])

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 工具目录
TOOLS_DIR = r"D:\tools"


@router.get("/paths")
async def get_paths():
    """获取项目文件夹和常用输出目录"""
    paths = {
        "project_root": PROJECT_ROOT,
        "data_dir": os.path.join(PROJECT_ROOT, "data"),
        "downloads_dir": os.path.join(PROJECT_ROOT, "downloads"),
        "output_dir": os.path.join(PROJECT_ROOT, "output"),
        "exports_dir": os.path.join(PROJECT_ROOT, "exports"),
        "tools_dir": TOOLS_DIR,
        "yt_dlp_path": os.path.join(TOOLS_DIR, "yt-dlp", "yt-dlp.exe"),
        "ffmpeg_path": os.path.join(TOOLS_DIR, "ffmpeg", "ffmpeg.exe"),
    }

    return {
        key: {
            "path": value,
            "exists": os.path.exists(value),
        }
        for key, value in paths.items()
    }
