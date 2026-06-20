# backend/tests/test_tooling.py
# 外部工具检测测试 - 验证 D:\tools 优先和 PATH 回退逻辑

import json
import os
import subprocess
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


class FakeBinaryOutputProcess:
    """测试用异常字符输出进程，模拟 Windows 下容错解码后的文本"""

    def __init__(self, output_path: str):
        self.stdout = [
            "[download]  50.0% of 10.00MiB\n",
            f'[Merger] Merging formats into "{output_path}" �\n',
        ]
        self.returncode = 0

    def wait(self):
        """模拟进程正常结束"""
        return self.returncode


class FakeSubtitleProcess:
    """测试用字幕进程，模拟 yt-dlp 成功退出但没有产出目标语言字幕"""

    def __init__(self):
        self.returncode = 0

    def communicate(self, timeout=None):
        """返回空输出，避免真实调用 yt-dlp"""
        return "", ""


class FakeAuthFailureProcess:
    """测试用下载进程，模拟 YouTube 要求登录验证"""

    def __init__(self):
        self.stdout = ["ERROR: [youtube] test: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies"]
        self.returncode = 1

    def wait(self):
        """模拟进程失败退出"""
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

    def test_parse_video_retries_with_browser_cookies_when_youtube_requires_auth(self):
        """解析遇到 YouTube 机器人验证时会自动尝试浏览器 cookies"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            """第一次模拟被拦截，第二次带浏览器 cookies 后成功"""
            calls.append(cmd)
            if "--cookies-from-browser" not in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr="ERROR: [youtube] test: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies",
                )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({
                    "id": "test",
                    "title": "测试视频",
                    "uploader": "测试作者",
                    "duration": 12,
                    "thumbnail": "https://example.test/cover.jpg",
                    "formats": [],
                    "subtitles": {},
                    "automatic_captions": {},
                }),
                stderr="",
            )

        with patch.dict(os.environ, {"YTV_YTDLP_COOKIES_FILE": "", "YTV_YTDLP_COOKIES_BROWSER": ""}), \
                patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                patch("backend.core.downloader.load_ytdlp_cookie_settings", return_value={"cookies_file": "", "cookies_browser": ""}), \
                patch("backend.core.downloader.subprocess.run", side_effect=fake_run):
            downloader = Downloader()
            result = downloader.parse_video("https://youtube.com/watch?v=test")

        self.assertEqual(result["title"], "测试视频")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--cookies-from-browser", calls[0])
        self.assertIn("--cookies-from-browser", calls[1])
        self.assertLess(calls[1].index("--cookies-from-browser"), calls[1].index("https://youtube.com/watch?v=test"))

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

    def test_download_video_uses_configured_browser_cookies(self):
        """下载视频时会使用用户配置的浏览器 cookies"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "merged.mp4")
            open(output_path, "wb").close()
            captured_cmd: list[str] = []

            def fake_popen(cmd, **_):
                """记录命令并返回模拟下载进程"""
                captured_cmd.extend(cmd)
                return FakeDownloadProcess(output_path)

            with patch.dict(os.environ, {"YTV_YTDLP_COOKIES_FILE": "", "YTV_YTDLP_COOKIES_BROWSER": "chrome"}), \
                    patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.get_ffmpeg_command", return_value="D:/tools/ffmpeg/ffmpeg.exe"), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                result = downloader.download_video(
                    url="https://youtube.com/watch?v=test",
                    output_dir=temp_dir,
                    format_id="137+140",
                )

        self.assertEqual(result, output_path)
        self.assertIn("--cookies-from-browser", captured_cmd)
        self.assertIn("chrome", captured_cmd)
        self.assertLess(captured_cmd.index("--cookies-from-browser"), captured_cmd.index("https://youtube.com/watch?v=test"))

    def test_download_video_uses_configured_cookies_file(self):
        """下载视频时优先使用用户配置的 cookies.txt 文件"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "merged.mp4")
            cookies_path = os.path.join(temp_dir, "cookies.txt")
            open(output_path, "wb").close()
            open(cookies_path, "w", encoding="utf-8").close()
            captured_cmd: list[str] = []

            def fake_popen(cmd, **_):
                """记录命令并返回模拟下载进程"""
                captured_cmd.extend(cmd)
                return FakeDownloadProcess(output_path)

            with patch.dict(os.environ, {"YTV_YTDLP_COOKIES_FILE": "", "YTV_YTDLP_COOKIES_BROWSER": ""}), \
                    patch("backend.core.downloader.load_ytdlp_cookie_settings", return_value={"cookies_file": cookies_path, "cookies_browser": ""}), \
                    patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.get_ffmpeg_command", return_value="D:/tools/ffmpeg/ffmpeg.exe"), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                result = downloader.download_video(
                    url="https://youtube.com/watch?v=test",
                    output_dir=temp_dir,
                    format_id="137+140",
                )

        self.assertEqual(result, output_path)
        self.assertIn("--cookies", captured_cmd)
        self.assertIn(cookies_path, captured_cmd)
        self.assertLess(captured_cmd.index("--cookies"), captured_cmd.index("https://youtube.com/watch?v=test"))

    def test_cookie_retry_message_summarizes_locked_chrome_database(self):
        """浏览器 cookies 数据库被占用时给出可操作中文提示"""
        with patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"):
            downloader = Downloader()
            message = downloader._cookie_retry_failure_message(
                "视频解析",
                "ERROR: Could not copy Chrome cookie database",
                ["ERROR: [youtube] test: Sign in to confirm you're not a bot."],
            )

        self.assertIn("Chrome/Edge cookies 数据库复制失败", message)
        self.assertIn("cookies.txt", message)

    def test_download_video_retries_with_browser_cookies_when_youtube_requires_auth(self):
        """下载遇到 YouTube 机器人验证时会自动重试浏览器 cookies"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "merged.mp4")
            open(output_path, "wb").close()
            calls: list[list[str]] = []

            def fake_popen(cmd, **_):
                """第一次模拟被拦截，第二次带浏览器 cookies 后成功"""
                calls.append(cmd)
                if "--cookies-from-browser" not in cmd:
                    return FakeAuthFailureProcess()
                return FakeDownloadProcess(output_path)

            with patch.dict(os.environ, {"YTV_YTDLP_COOKIES_FILE": "", "YTV_YTDLP_COOKIES_BROWSER": ""}), \
                    patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.get_ffmpeg_command", return_value="D:/tools/ffmpeg/ffmpeg.exe"), \
                    patch("backend.core.downloader.load_ytdlp_cookie_settings", return_value={"cookies_file": "", "cookies_browser": ""}), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                result = downloader.download_video(
                    url="https://youtube.com/watch?v=test",
                    output_dir=temp_dir,
                    format_id="137+140",
                )

        self.assertEqual(result, output_path)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--cookies-from-browser", calls[0])
        self.assertIn("--cookies-from-browser", calls[1])

    def test_download_video_replaces_invalid_utf8_output(self):
        """下载器输出包含非 UTF-8 字节时不会中断任务"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "中文视频.mp4")
            open(output_path, "wb").close()
            captured_cmd: list[str] = []

            def fake_popen(cmd, **kwargs):
                """断言子进程输出使用容错解码"""
                captured_cmd.extend(cmd)
                self.assertEqual(kwargs.get("encoding"), "utf-8")
                self.assertEqual(kwargs.get("errors"), "replace")
                return FakeBinaryOutputProcess(output_path)

            with patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.get_ffmpeg_command", return_value="D:/tools/ffmpeg/ffmpeg.exe"), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                result = downloader.download_video(
                    url="https://youtube.com/watch?v=test",
                    output_dir=temp_dir,
                )

        self.assertEqual(result, output_path)
        self.assertIn("bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080][ext=mp4]/best[height<=1080]/best", captured_cmd)

    def test_download_subtitle_does_not_return_unrelated_old_subtitle(self):
        """目标语言没有新字幕文件时，不会误返回输出目录里的旧字幕"""
        with tempfile.TemporaryDirectory() as temp_dir:
            old_subtitle = os.path.join(temp_dir, "old.en.vtt")
            with open(old_subtitle, "w", encoding="utf-8") as file:
                file.write("WEBVTT\n")
            captured_cmd: list[str] = []

            def fake_popen(cmd, **_):
                """记录命令并返回没有产物的字幕进程"""
                captured_cmd.extend(cmd)
                return FakeSubtitleProcess()

            with patch("backend.core.downloader.get_yt_dlp_command", return_value="yt-dlp"), \
                    patch("backend.core.downloader.subprocess.Popen", side_effect=fake_popen):
                downloader = Downloader()
                with self.assertRaises(RuntimeError) as context:
                    downloader.download_subtitle(
                        url="https://youtube.com/watch?v=test",
                        output_dir=temp_dir,
                        language="zh-Hans",
                    )

        self.assertIn("字幕下载完成但未找到文件", str(context.exception))
        self.assertIn("--force-overwrites", captured_cmd)
        self.assertIn("--socket-timeout", captured_cmd)
        self.assertIn("20", captured_cmd)
        self.assertIn("zh-Hans", captured_cmd)


if __name__ == "__main__":
    unittest.main()
