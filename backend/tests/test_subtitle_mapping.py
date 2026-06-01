# backend/tests/test_subtitle_mapping.py
# 字幕映射测试 - 使用标准库验证文本 API 处理后仍尽量保留原字幕时间轴

import unittest
import os
import sys


# 嵌入式 Python 默认可能不读取 PYTHONPATH，测试文件直接运行时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.automation import map_text_to_timed_entries


class SubtitleMappingTest(unittest.TestCase):
    """字幕时间轴映射测试"""

    def test_map_same_line_count_keeps_each_timestamp(self):
        """行数一致时，每行沿用对应原字幕时间"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "a"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "b"},
        ]

        mapped = map_text_to_timed_entries("第一句\n第二句", original)

        self.assertEqual([item["start"] for item in mapped], ["00:00:01,000", "00:00:02,000"])
        self.assertEqual([item["end"] for item in mapped], ["00:00:02,000", "00:00:03,000"])
        self.assertEqual([item["text"] for item in mapped], ["第一句", "第二句"])

    def test_map_fewer_lines_merges_timestamp_ranges(self):
        """AI 返回行数更少时，字幕会覆盖原时间段范围"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "a"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "b"},
            {"index": 3, "start": "00:00:03,000", "end": "00:00:04,000", "text": "c"},
            {"index": 4, "start": "00:00:04,000", "end": "00:00:05,000", "text": "d"},
        ]

        mapped = map_text_to_timed_entries("上半段\n下半段", original)

        self.assertEqual(mapped[0]["start"], "00:00:01,000")
        self.assertEqual(mapped[0]["end"], "00:00:03,000")
        self.assertEqual(mapped[1]["start"], "00:00:03,000")
        self.assertEqual(mapped[1]["end"], "00:00:05,000")

    def test_map_more_lines_distributes_text_into_original_slots(self):
        """AI 返回行数更多时，多行文本会分配回原字幕槽位"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "a"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "b"},
        ]

        mapped = map_text_to_timed_entries("第一句\n第二句\n第三句\n第四句", original)

        self.assertEqual(len(mapped), 2)
        self.assertEqual(mapped[0]["start"], "00:00:01,000")
        self.assertEqual(mapped[0]["end"], "00:00:02,000")
        self.assertEqual(mapped[0]["text"], "第一句\n第二句")
        self.assertEqual(mapped[1]["start"], "00:00:02,000")
        self.assertEqual(mapped[1]["end"], "00:00:03,000")
        self.assertEqual(mapped[1]["text"], "第三句\n第四句")


if __name__ == "__main__":
    unittest.main()
