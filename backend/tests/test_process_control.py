# backend/tests/test_process_control.py
# 进程控制测试 - 验证持久化 PID 和取消请求能跨后端实例工作

import os
import sqlite3
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core import process_control  # noqa: E402
from backend.core.process_control import clear_control_request, register_process, request_control, requested_action, unregister_process  # noqa: E402


class FakeProcess:
    """测试用 Popen 替身，只保留进程控制需要的字段"""

    def __init__(self, pid: int):
        self.pid = pid
        self.args = ["ffmpeg", "-i", "input.mp4", "output.mp4"]
        self.returncode = None

    def poll(self):
        return self.returncode


class ProcessControlTests(unittest.TestCase):
    """进程控制持久化测试"""

    def setUp(self):
        process_control.ensure_runtime_tables()
        with sqlite3.connect(process_control.DB_PATH) as connection:
            connection.execute("DELETE FROM runtime_processes")
            connection.execute("DELETE FROM runtime_control_requests")
        process_control._processes_by_key.clear()
        process_control._keys_by_process.clear()
        process_control._requested_actions.clear()

    def tearDown(self):
        with sqlite3.connect(process_control.DB_PATH) as connection:
            connection.execute("DELETE FROM runtime_processes")
            connection.execute("DELETE FROM runtime_control_requests")
        process_control._processes_by_key.clear()
        process_control._keys_by_process.clear()
        process_control._requested_actions.clear()

    def test_register_process_writes_runtime_record_and_unregister_removes_it(self):
        """登记进程会写入 SQLite，结束后会清理记录"""
        process = FakeProcess(43210)

        register_process(["task:9", "job:auto-1"], process, process.args)
        with sqlite3.connect(process_control.DB_PATH) as connection:
            rows = connection.execute("SELECT pid, keys_json, command_line FROM runtime_processes").fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 43210)
        self.assertIn("task:9", rows[0][1])
        self.assertIn("input.mp4", rows[0][2])

        unregister_process(process)
        with sqlite3.connect(process_control.DB_PATH) as connection:
            count = connection.execute("SELECT COUNT(*) FROM runtime_processes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_request_control_kills_persistent_pid_and_persists_action(self):
        """即使内存里没有 Popen 对象，也能按持久化 PID 取消旧进程"""
        with sqlite3.connect(process_control.DB_PATH) as connection:
            connection.execute(
                "INSERT INTO runtime_processes (pid, parent_pid, keys_json, command_line) VALUES (?, ?, ?, ?)",
                (45678, 1, '["task:15"]', "ffmpeg -i old.mp4 out.mp4"),
            )

        with patch("backend.core.process_control.terminate_process_tree", return_value=True) as kill_mock:
            killed_count = request_control("task:15", "cancel")

        self.assertEqual(killed_count, 1)
        kill_mock.assert_called_once_with(45678)
        self.assertEqual(requested_action(["task:15"]), "cancel")

        clear_control_request("task:15")
        self.assertIsNone(requested_action(["task:15"]))

    def test_terminate_matching_tool_processes_matches_command_line_fragment(self):
        """旧任务没有 PID 记录时，可按输入输出路径兜底杀进程"""
        processes = [
            {"ProcessId": 111, "Name": "ffmpeg.exe", "CommandLine": "ffmpeg -i D:/video/input.mp4 D:/video/out.mp4"},
            {"ProcessId": 222, "Name": "ffmpeg.exe", "CommandLine": "ffmpeg -i D:/other/input.mp4 D:/other/out.mp4"},
        ]

        with patch("backend.core.process_control._list_tool_processes", return_value=processes), \
                patch("backend.core.process_control.terminate_process_tree", return_value=True) as kill_mock:
            killed_count = process_control.terminate_matching_tool_processes(["D:\\video\\input.mp4"])

        self.assertEqual(killed_count, 1)
        kill_mock.assert_called_once_with(111)


if __name__ == "__main__":
    unittest.main()
