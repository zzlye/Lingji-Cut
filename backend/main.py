# backend/main.py
# FastAPI 后端入口 - 提供视频解析、下载、字幕、配音、导出等 API
# 启动命令：python main.py 或 uvicorn main:app --host 127.0.0.1 --port 8765

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .models import init_db
from .api import automation_router, videos_router, tasks_router, subtitles_router, profiles_router, voice_router, exports_router, effects_router, settings_router
from .api.automation import recover_automation_jobs_on_startup

# 创建 FastAPI 应用实例
app = FastAPI(
    title="YouTube 视频处理器",
    description="YouTube 视频下载、字幕、配音、导出处理后端",
    version="1.0.0"
)

# 配置 CORS（允许 Electron 渲染进程访问）
app.add_middleware(
    CORSMiddleware,
    # 允许所有来源（本地桌面应用）
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(videos_router)
app.include_router(tasks_router)
app.include_router(subtitles_router)
app.include_router(profiles_router)
app.include_router(voice_router)
app.include_router(exports_router)
app.include_router(effects_router)
app.include_router(settings_router)
app.include_router(automation_router)


@app.on_event("startup")
async def startup():
    """应用启动时初始化数据库，并恢复未完成的一键任务"""
    init_db()
    recover_automation_jobs_on_startup()


@app.get("/health")
async def health_check():
    """健康检查端点 - 用于 Electron 主进程检测后端是否就绪"""
    return {"status": "ok", "service": "youtube-video-processor"}


@app.get("/")
async def root():
    """根端点 - 返回服务信息"""
    return {
        "name": "YouTube 视频处理器",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "videos": "/videos",
            "tasks": "/tasks",
            "subtitles": "/subtitles",
            "voice": "/voice",
            "exports": "/exports",
            "profiles": "/profiles",
            "effects": "/effects",
            "settings": "/settings",
            "automation": "/automation"
        }
    }


# 启动入口
if __name__ == "__main__":
    # 启动 uvicorn 服务器，监听本地 8765 端口
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info"
    )
