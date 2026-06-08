# backend/core/tooling.py
# 外部工具检测 - 统一检查 yt-dlp 和 ffmpeg 是否可用

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


TOOLS_DIR = r"D:\tools"
YT_DLP_PATH = os.path.join(TOOLS_DIR, "yt-dlp", "yt-dlp.exe")
FFMPEG_PATH = os.path.join(TOOLS_DIR, "ffmpeg", "ffmpeg.exe")


@dataclass
class ToolStatus:
    """外部工具状态"""

    name: str
    command: str
    available: bool
    version: Optional[str] = None
    source: str = "missing"
    error_message: Optional[str] = None


def _bundled_tool_path(exe_name: Optional[str]) -> Optional[str]:
    """打包环境下从 Electron 通过 YTV_TOOLS_DIR 指定的随附工具目录查找"""
    if not exe_name:
        return None
    base = os.environ.get("YTV_TOOLS_DIR")
    if base:
        candidate = os.path.join(base, exe_name)
        if os.path.exists(candidate):
            return candidate
    return None


def resolve_tool_command(preferred_path: str, fallback_name: str, bundled_exe: Optional[str] = None) -> tuple[str, str, bool]:
    """解析工具命令：优先随包目录（打包环境），其次 D:\\tools，最后回退 PATH"""
    bundled = _bundled_tool_path(bundled_exe)
    if bundled:
        return bundled, "bundled", True

    if os.path.exists(preferred_path):
        return preferred_path, "D:\\tools", True

    path_command = shutil.which(fallback_name)
    if path_command:
        return path_command, "PATH", True

    return fallback_name, "missing", False


def get_yt_dlp_command() -> str:
    """获取 yt-dlp 命令路径"""
    return resolve_tool_command(YT_DLP_PATH, "yt-dlp", "yt-dlp.exe")[0]


def get_ffmpeg_command() -> str:
    """获取 ffmpeg 命令路径"""
    return resolve_tool_command(FFMPEG_PATH, "ffmpeg", "ffmpeg.exe")[0]


def check_tool(name: str, preferred_path: str, fallback_name: str, version_args: list[str], bundled_exe: Optional[str] = None) -> ToolStatus:
    """检查单个外部工具是否可执行"""
    command, source, exists = resolve_tool_command(preferred_path, fallback_name, bundled_exe)
    if not exists:
        return ToolStatus(
            name=name,
            command=command,
            available=False,
            source=source,
            error_message=f"未找到 {name}，请安装到 D:\\tools 或加入 PATH",
        )

    try:
        result = subprocess.run(
            [command, *version_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            return ToolStatus(
                name=name,
                command=command,
                available=False,
                source=source,
                error_message=output or f"{name} 执行失败",
            )
        version = output.splitlines()[0] if output else None
        return ToolStatus(name=name, command=command, available=True, version=version, source=source)
    except Exception as exc:
        return ToolStatus(name=name, command=command, available=False, source=source, error_message=str(exc))


def check_required_tools() -> dict[str, ToolStatus]:
    """检查自动化流程必需的外部工具"""
    return {
        "yt_dlp": check_tool("yt-dlp", YT_DLP_PATH, "yt-dlp", ["--version"], "yt-dlp.exe"),
        "ffmpeg": check_tool("ffmpeg", FFMPEG_PATH, "ffmpeg", ["-version"], "ffmpeg.exe"),
    }


def assert_required_tools_available(require_yt_dlp: bool = True) -> None:
    """自动化任务启动前校验必需工具"""
    statuses = check_required_tools()
    if not require_yt_dlp:
        statuses.pop("yt_dlp", None)
    missing = [status for status in statuses.values() if not status.available]
    if missing:
        detail = "；".join(status.error_message or f"{status.name} 不可用" for status in missing)
        raise RuntimeError(f"自动化环境未就绪: {detail}")
