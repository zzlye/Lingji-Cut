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

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
