# backend/api/__init__.py
# API 路由包初始化 - 注册所有 API 路由

from .videos import router as videos_router
from .tasks import router as tasks_router
from .subtitles import router as subtitles_router
from .profiles import router as profiles_router
from .voice import router as voice_router
from .exports import router as exports_router
from .effects import router as effects_router
from .settings import router as settings_router
from .automation import router as automation_router
from .logs import router as logs_router

__all__ = [
    "videos_router",
    "tasks_router",
    "subtitles_router",
    "profiles_router",
    "voice_router",
    "exports_router",
    "effects_router",
    "settings_router",
    "automation_router",
    "logs_router",
]
