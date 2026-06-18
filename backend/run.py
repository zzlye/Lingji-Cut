# backend/run.py
# 嵌入式 Python 启动器 - 兼容 D:\tools\python-3.12.10-embed 的隔离 sys.path

import os
import socket
import subprocess
import sys
import time

import uvicorn


def _port_in_use(host: str, port: int) -> bool:
    """检测端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _free_occupied_port(host: str, port: int) -> None:
    """启动前清掉占用本端口的遗留后端进程，避免端口冲突导致反复重启失败（仅 Windows）

    后端独占本机 8765 端口，占用者只可能是上一次没退干净的旧后端僵尸，
    启动前杀掉它再 bind 最稳，否则会出现旧进程霸占端口、新进程反复 bind 失败的死循环。
    """
    if os.name != "nt" or not _port_in_use(host, port):
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception:
        return
    my_pid = os.getpid()
    stale_pids: set[int] = set()
    target = f"{host}:{port}"
    for line in (result.stdout or "").splitlines():
        # 只杀监听该端口的进程，跳过 ESTABLISHED/TIME_WAIT 等连接记录
        if target in line and "LISTENING" in line.upper():
            parts = line.split()
            try:
                pid = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if pid > 0 and pid != my_pid:
                stale_pids.add(pid)
    for pid in stale_pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
            print(f"[run] 已清理占用端口 {port} 的遗留后端进程 PID={pid}", flush=True)
        except Exception:
            pass
    # 等待端口释放，最多约 5 秒
    for _ in range(20):
        if not _port_in_use(host, port):
            return
        time.sleep(0.25)


def main() -> None:
    """启动 FastAPI 后端服务"""
    # 将项目根目录加入模块搜索路径，确保 backend 包可以被嵌入式 Python 找到。
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 默认只监听本机，服务器部署时可通过环境变量改成 0.0.0.0。
    host = os.environ.get("LINGJIAN_HOST", "127.0.0.1")
    port = int(os.environ.get("LINGJIAN_PORT", "8765"))

    # 启动前清理残留、占着端口的旧后端进程，避免端口冲突反复重启失败
    _free_occupied_port(host, port)

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
