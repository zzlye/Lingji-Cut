# backend/run.py
# 嵌入式 Python 启动器 - 兼容 D:\tools\python-3.12.10-embed 的隔离 sys.path

import os
import sys

import uvicorn


def main() -> None:
    """启动 FastAPI 后端服务"""
    # 将项目根目录加入模块搜索路径，确保 backend 包可以被嵌入式 Python 找到。
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 默认只监听本机，服务器部署时可通过环境变量改成 0.0.0.0。
    host = os.environ.get("LINGJIAN_HOST", "127.0.0.1")
    port = int(os.environ.get("LINGJIAN_PORT", "8765"))

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
