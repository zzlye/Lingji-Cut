# backend/tests/test_subtitle_correction.py
# 字幕校对测试 - 验证手动修正字幕的解析、保存和 ASS 生成能力

import asyncio
import os
import sys
import tempfile
import unittest


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.subtitles import SubtitleCorrectionSaveAssRequest, SubtitleCorrectionSaveRequest, SubtitleEntryPayload, save_corrected_ass, save_corrected_subtitle  # noqa: E402
from backend.core.subtitle_engine import SubtitleEngine  # noqa: E402


class EmptyQuery:
    """测试用空查询对象，用于模拟没有保存字幕预设的数据库"""

    def filter(self, *_):
        return self

    def first(self):
        return None


class EmptyDb:
    """测试用空数据库会话"""

    def query(self, *_):
        return EmptyQuery()


class SubtitleCorrectionTests(unittest.TestCase):
    """字幕校对能力测试"""

    def test_parse_srt_text_normalizes_entries(self):
        """粘贴 SRT 文本后能解析为标准条目"""
        content = """1
00:00:01,000 --> 00:00:02,500
第一句字幕

2
00:00:03.000 --> 00:00:04.250
第二句字幕
"""

        entries = SubtitleEngine().parse_srt_content(content)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1]["start"], "00:00:03,000")
        self.assertEqual(entries[1]["end"], "00:00:04,250")
        self.assertEqual(entries[0]["text"], "第一句字幕")

    def test_save_srt_then_parse_keeps_text_and_time(self):
        """保存校对后的 SRT 后再次解析，时间轴和文本保持一致"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "fixed.srt")
            request = SubtitleCorrectionSaveRequest(
                output_path=output_path,
                entries=[
                    SubtitleEntryPayload(index=1, start="00:00:01.000", end="00:00:02.500", text="修正后的字幕"),
                    SubtitleEntryPayload(index=2, start="00:00:03,000", end="00:00:04,250", text="第二句字幕"),
                ],
            )

            response = asyncio.run(save_corrected_subtitle(request))
            reparsed = SubtitleEngine().parse_srt(response.output_path)

            self.assertEqual(response.output_path, output_path)
            self.assertEqual(reparsed[0]["start"], "00:00:01,000")
            self.assertEqual(reparsed[0]["end"], "00:00:02,500")
            self.assertEqual(reparsed[0]["text"], "修正后的字幕")

    def test_save_ass_generates_file(self):
        """校对后的字幕可以直接生成 ASS 文件"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "fixed.ass")
            request = SubtitleCorrectionSaveAssRequest(
                output_path=output_path,
                entries=[
                    SubtitleEntryPayload(index=1, start="00:00:01,000", end="00:00:02,000", text="ASS 字幕"),
                ],
            )

            response = asyncio.run(save_corrected_ass(request, EmptyDb()))

            self.assertEqual(response.output_path, output_path)
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Dialogue:", content)
            self.assertIn("ASS 字幕", content)


if __name__ == "__main__":
    unittest.main()
