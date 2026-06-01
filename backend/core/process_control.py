# backend/core/process_control.py
# 长任务进程控制 - 持久化登记 yt-dlp/ffmpeg 子进程，支持跨后端实例暂停和取消

import json
import os
import signal
import sqlite3
import subprocess
import time
from threading import RLock
from typing import Any, Iterable, Optional

from ..models.database import DB_PATH


class TaskControlRequested(Exception):
    """任务被用户请求暂停或取消"""

    def __init__(self, action: str):
        self.action = action
        message = {"pause": "任务已暂停", "skip": "任务已跳过"}.get(action, "任务已取消")
        super().__init__(message)


# 进程表使用字符串 key，格式为 job:<id> 或 task:<id>。
_processes_by_key: dict[str, set[subprocess.Popen]] = {}
_keys_by_process: dict[int, set[str]] = {}
_requested_actions: dict[str, str] = {}
_lock = RLock()
_tables_ready = False


def ensure_runtime_tables() -> None:
    """创建运行时进程表和控制请求表，保证后端重启后仍能找到旧进程"""
    global _tables_ready
    with _lock:
        if _tables_ready and os.path.exists(DB_PATH):
            return
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_processes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pid INTEGER NOT NULL,
                    parent_pid INTEGER,
                    keys_json TEXT NOT NULL,
                    command_line TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_control_requests (
                    key TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        _tables_ready = True


def normalize_control_keys(keys: Optional[Iterable[str]]) -> list[str]:
    """清理控制 key，保证后续查找稳定"""
    return [str(key) for key in (keys or []) if key]


def subprocess_creation_flags() -> int:
    """为外部工具创建独立进程组，方便 Windows 下整棵进程树终止"""
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def register_process(keys: Iterable[str], process: subprocess.Popen, command: Optional[Any] = None) -> None:
    """登记一个可被用户暂停或取消的子进程"""
    normalized = normalize_control_keys(keys)
    if not normalized:
        return
    with _lock:
        _keys_by_process[id(process)] = set(normalized)
        for key in normalized:
            _processes_by_key.setdefault(key, set()).add(process)
        _save_runtime_process(process.pid, os.getpid(), normalized, _command_to_string(command or getattr(process, "args", "")))


def unregister_process(process: subprocess.Popen) -> None:
    """子进程结束后从控制表移除"""
    with _lock:
        keys = _keys_by_process.pop(id(process), set())
        for key in keys:
            processes = _processes_by_key.get(key)
            if not processes:
                continue
            processes.discard(process)
            if not processes:
                _processes_by_key.pop(key, None)
        pid = getattr(process, "pid", None)
        if pid:
            _delete_runtime_process(int(pid))


def request_control(key: str, action: str) -> int:
    """请求暂停或取消，并终止当前登记和持久化记录里的子进程"""
    if action not in {"pause", "cancel", "skip"}:
        raise ValueError(f"不支持的任务控制动作: {action}")
    with _lock:
        _requested_actions[key] = action
        _save_control_request(key, action)
        processes = list(_processes_by_key.get(key, set()))
        process_pids = {int(process.pid) for process in processes if getattr(process, "pid", None)}
        persistent_pids = _runtime_pids_for_key(key)

    killed_pids: set[int] = set()
    for process in processes:
        if terminate_process(process):
            killed_pids.add(int(process.pid))

    for pid in persistent_pids - process_pids:
        if terminate_process_tree(pid):
            killed_pids.add(pid)

    if killed_pids:
        _delete_runtime_processes(killed_pids)
    return len(killed_pids)


def clear_control_request(key: str) -> None:
    """清除某个任务的暂停/取消请求"""
    with _lock:
        _requested_actions.pop(key, None)
        _delete_control_request(key)


def requested_action(keys: Iterable[str]) -> Optional[str]:
    """读取任意控制 key 上的暂停或取消请求"""
    normalized = normalize_control_keys(keys)
    with _lock:
        for key in normalized:
            action = _requested_actions.get(key)
            if action:
                return action
        return _persistent_requested_action(normalized)


def raise_if_control_requested(keys: Iterable[str]) -> None:
    """在长任务阶段边界主动检查用户控制请求"""
    action = requested_action(keys)
    if action:
        raise TaskControlRequested(action)


def terminate_process(process: subprocess.Popen, grace_seconds: float = 2.0) -> bool:
    """温和终止子进程，超时后强制结束"""
    if process.poll() is not None:
        return False

    if os.name == "nt":
        # Windows 下 yt-dlp 会再拉起 ffmpeg，使用 taskkill 才能一起结束子进程。
        return terminate_process_tree(int(process.pid))

    try:
        process.terminate()
    except Exception:
        return False

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)

    if process.poll() is None:
        try:
            process.kill()
            return True
        except Exception:
            return False
    return True


def terminate_process_tree(pid: int, grace_seconds: float = 2.0) -> bool:
    """按 PID 终止整棵进程树，主要用于 ffmpeg/yt-dlp 长任务"""
    if not pid or pid == os.getpid():
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except Exception:
        return False

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _pid_running(pid):
            return True
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False


def terminate_matching_tool_processes(fragments: Iterable[str], tool_names: Iterable[str] = ("ffmpeg.exe", "yt-dlp.exe")) -> int:
    """按命令行片段兜底终止外部工具进程，用于清理旧版本遗留任务"""
    normalized_fragments = [_normalize_match_text(fragment) for fragment in fragments if _valid_match_fragment(fragment)]
    if not normalized_fragments:
        return 0

    killed_pids: set[int] = set()
    for item in _list_tool_processes(tool_names):
        command_line = _normalize_match_text(str(item.get("CommandLine") or ""))
        if not command_line:
            continue
        if not any(fragment in command_line for fragment in normalized_fragments):
            continue
        pid = int(item.get("ProcessId") or 0)
        if terminate_process_tree(pid):
            killed_pids.add(pid)

    if killed_pids:
        _delete_runtime_processes(killed_pids)
    return len(killed_pids)


def cleanup_stale_runtime_processes() -> int:
    """删除已经不存在的运行时进程记录，避免任务控制表越来越脏"""
    stale_pids: set[int] = set()
    for row in _runtime_process_rows():
        pid = int(row["pid"])
        if not _pid_running(pid):
            stale_pids.add(pid)
    if stale_pids:
        _delete_runtime_processes(stale_pids)
    return len(stale_pids)


def _save_runtime_process(pid: int, parent_pid: int, keys: list[str], command_line: str) -> None:
    """把外部工具 PID 写入 SQLite，供其他后端进程取消"""
    ensure_runtime_tables()
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute("DELETE FROM runtime_processes WHERE pid = ?", (pid,))
        connection.execute(
            """
            INSERT INTO runtime_processes (pid, parent_pid, keys_json, command_line)
            VALUES (?, ?, ?, ?)
            """,
            (pid, parent_pid, json.dumps(keys, ensure_ascii=False), command_line),
        )


def _delete_runtime_process(pid: int) -> None:
    """按 PID 删除运行时进程记录"""
    _delete_runtime_processes({int(pid)})


def _delete_runtime_processes(pids: Iterable[int]) -> None:
    """批量删除运行时进程记录"""
    normalized = [int(pid) for pid in pids if pid]
    if not normalized:
        return
    ensure_runtime_tables()
    placeholders = ",".join("?" for _ in normalized)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(f"DELETE FROM runtime_processes WHERE pid IN ({placeholders})", normalized)


def _runtime_pids_for_key(key: str) -> set[int]:
    """读取包含指定控制 key 的持久化 PID"""
    pids: set[int] = set()
    for row in _runtime_process_rows():
        try:
            keys = json.loads(row["keys_json"])
        except json.JSONDecodeError:
            keys = []
        if key in keys:
            pids.add(int(row["pid"]))
    return pids


def _runtime_process_rows() -> list[sqlite3.Row]:
    """读取所有运行时进程记录"""
    ensure_runtime_tables()
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute("SELECT pid, keys_json, command_line FROM runtime_processes"))


def _save_control_request(key: str, action: str) -> None:
    """把暂停/取消请求写入 SQLite，让正在跑的其他后端实例也能读到"""
    ensure_runtime_tables()
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            """
            INSERT INTO runtime_control_requests (key, action, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET action = excluded.action, updated_at = CURRENT_TIMESTAMP
            """,
            (key, action),
        )


def _delete_control_request(key: str) -> None:
    """删除持久化控制请求"""
    ensure_runtime_tables()
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute("DELETE FROM runtime_control_requests WHERE key = ?", (key,))


def _persistent_requested_action(keys: list[str]) -> Optional[str]:
    """从 SQLite 读取暂停/取消请求"""
    if not keys:
        return None
    ensure_runtime_tables()
    placeholders = ",".join("?" for _ in keys)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        rows = connection.execute(
            f"SELECT action FROM runtime_control_requests WHERE key IN ({placeholders}) ORDER BY updated_at DESC",
            keys,
        ).fetchall()
    for row in rows:
        action = row[0]
        if action in {"pause", "cancel", "skip"}:
            return str(action)
    return None


def _command_to_string(command: Any) -> str:
    """把 Popen 参数整理成可搜索的命令行文本"""
    if isinstance(command, (list, tuple)):
        return subprocess.list2cmdline([str(item) for item in command])
    return str(command or "")


def _list_tool_processes(tool_names: Iterable[str]) -> list[dict[str, Any]]:
    """列出当前系统里的外部工具进程"""
    names = {str(name).lower() for name in tool_names if name}
    if os.name != "nt":
        return _list_tool_processes_posix(names)

    quoted_names = ",".join(f"'{name}'" for name in sorted(names))
    script = (
        f"$names=@({quoted_names});"
        "Get-CimInstance Win32_Process | "
        "Where-Object { $names -contains $_.Name.ToLower() } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def _list_tool_processes_posix(names: set[str]) -> list[dict[str, Any]]:
    """类 Unix 环境下用 ps 兜底列出外部工具进程"""
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,comm=,args="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return []
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid, parent_pid, name, command_line = parts
        if os.path.basename(name).lower() not in names:
            continue
        processes.append({
            "ProcessId": int(pid),
            "ParentProcessId": int(parent_pid),
            "Name": name,
            "CommandLine": command_line,
        })
    return processes


def _valid_match_fragment(fragment: str) -> bool:
    """过滤过短片段，避免误杀无关 ffmpeg 进程"""
    text = str(fragment or "").strip()
    return len(text) >= 8


def _normalize_match_text(value: str) -> str:
    """统一大小写和路径分隔符，提升命令行匹配稳定性"""
    return str(value or "").strip().lower().replace("/", "\\")


def _pid_running(pid: int) -> bool:
    """判断 PID 是否仍存在"""
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
