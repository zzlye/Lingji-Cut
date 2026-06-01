# backend/tests/test_tooling.py
# 外部工具检测测试 - 验证 D:\tools 优先和 PATH 回退逻辑

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.downloader import Downloader  # noqa: E402
from backend.core.tooling import check_tool, resolve_tool_command  # noqa: E402


class FakeDownloadProcess:
    """测试用下载进程，避免真正调用 yt-dlp"""

    def __init__(self, output_path: str):
        self.stdout = [f'[Merger] Merging formats into "{output_path}"']
        self.returncode = 0

    def wait(self):
        """模拟进程正常结束"""
        return self.returncode


class ToolingTests(unittest.TestCase):
    """外部工具检测测试"""

    def test_resolve_tool_prefers_existing_d_tools_path(self):
        """存在首选路径时优先使用该路径"""
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            preferred = temp_file.name
        try:
            command, source, exists = resolve_tool_command(preferred, "missing-tool")

            self.assertEqual(command, preferred)
            self.assertEqual(source, "D:\\tools")
            self.assertTrue(exists)
        finally:
            os.remove(preferred)

    def test_resolve_tool_falls_back_to_path(self):
        """首选路径不存在时回退 PATH"""
        with patch("shutil.which", return_value="C:/bin/tool.exe"):
            command, source, exists = resolve_tool_command("D:/missing/tool.exe", "tool")

        self.assertEqual(command, "C:/bin/tool.exe")
        self.assertEqual(source, "PATH")
        self.assertTrue(exists)

    def test_check_tool_reports_missing(self):
        """首选路径和 PATH 都没有时返回不可用状态"""
        with patch("shutil.which", return_value=None):
            status = check_tool("demo", "D:/missing/demo.exe", "demo", ["--version"])

        self.assertFalse(status.available)
        self.assertEqual(status.source, "missing")
        self.assertIn("未找到 demo", status.error_message or "")

    def test_download_video_adds_ffmpeg_location_for_custom_format(self):
        """指定下载格式时仍然传入本地 ffmpeg 位置"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "merged.mp4")
            open(output_path, "wb").close()
            captured_cmd: list[str] = []

            def fake_popen(cmd, **_):
                """记录命令并返回模拟下载进程"""
                captured_cmd.extend(cmd)
                return FakeDownloadProcess(output_path)

            with patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.get_ffmpeg_command", return_value="D:/tools/ffmpeg/ffmpeg.exe"), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                result = downloader.download_video(
                    url="https://youtube.com/watch?v=test",
                    output_dir=temp_dir,
                    format_id="137+140",
                )

        self.assertEqual(result, output_path)
        self.assertIn("--ffmpeg-location", captured_cmd)
        self.assertIn("D:/tools/ffmpeg", captured_cmd)


if __name__ == "__main__":
    unittest.main()
