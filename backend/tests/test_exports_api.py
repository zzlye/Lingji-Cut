# backend/tests/test_exports_api.py
# 导出接口测试 - 验证手动导出不会绕过字幕清理

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.exports import ExportRequest, create_export  # noqa: E402


class FakeExportDb:
    """测试用导出数据库会话，只记录任务状态变化"""

    def __init__(self):
        self.tasks = []
        self.commit_count = 0

    def add(self, task):
        task.id = len(self.tasks) + 1
        self.tasks.append(task)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _task):
        return None


class FakeExportProcessor:
    """测试用导出处理器，避免真实启动 ffmpeg"""

    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.burn_calls: list[dict] = []

    def burn_subtitles(self, **kwargs):
        """记录字幕烧录参数并返回假视频路径"""
        self.burn_calls.append(kwargs)
        output_path = os.path.join(self.temp_dir, "subtitled.mp4")
        with open(output_path, "wb") as file:
            file.write(b"subtitled")
        return output_path

    def convert_format(self, input_path, output_format, control_keys=None, progress_callback=None):
        """记录最终导出并返回假成品路径"""
        if progress_callback:
            progress_callback(100)
        output_path = os.path.join(self.temp_dir, f"exported.{output_format}")
        with open(output_path, "wb") as file:
            file.write(b"exported")
        return output_path


class ExportApiTests(unittest.TestCase):
    """导出接口能力测试"""

    def test_create_export_cleans_ass_subtitle_before_burn(self):
        """手动导出旧 ASS 时先清理逗号、句号、省略号和顿号再烧录"""
        with tempfile.TemporaryDirectory(prefix="export_api_") as temp_dir:
            video_path = os.path.join(temp_dir, "input.mp4")
            subtitle_path = os.path.join(temp_dir, "manual.ass")
            with open(video_path, "wb") as file:
                file.write(b"video")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,这里还有，句号。省略号...和、顿号
""")

            fake_processor = FakeExportProcessor(temp_dir)
            with patch("backend.api.exports.FFmpegProcessor", return_value=fake_processor):
                response = create_export(
                    ExportRequest(video_path=video_path, subtitle_path=subtitle_path, output_format="mp4"),
                    FakeExportDb(),
                )

            self.assertTrue(response.output_path.endswith("exported.mp4"))
            cleaned_path = fake_processor.burn_calls[0]["subtitle_path"]
            self.assertNotEqual(cleaned_path, subtitle_path)
            self.assertTrue(cleaned_path.endswith("_export_clean.ass"))

            with open(cleaned_path, "r", encoding="utf-8") as file:
                cleaned_content = file.read()
            dialogue_text = cleaned_content.split("Dialogue:", 1)[1].rsplit(",,", 1)[1]
            self.assertNotRegex(dialogue_text, r"[，。、,.]|\.{3,}|…")
            self.assertIn("这里还有 句号 省略号 和 顿号", dialogue_text)


if __name__ == "__main__":
    unittest.main()
