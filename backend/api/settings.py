# backend/api/settings.py
# 设置 API 路由 - 提供项目文件夹信息

import os

from fastapi import APIRouter


router = APIRouter(prefix="/settings", tags=["settings"])

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@router.get("/paths")
async def get_paths():
    """获取项目目录，并确保常用子文件夹自动创建"""
    for dirname in ("data", "downloads", "output", "exports"):
        os.makedirs(os.path.join(PROJECT_ROOT, dirname), exist_ok=True)

    paths = {
        "project_root": PROJECT_ROOT,
    }

    return {
        key: {
            "path": value,
            "exists": os.path.exists(value),
        }
        for key, value in paths.items()
    }
