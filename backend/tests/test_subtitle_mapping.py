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

    def test_map_fewer_lines_distributes_text_without_merging_timestamps(self):
        """AI 返回行数更少时也必须保留原字幕槽位，避免一条字幕覆盖几十秒"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "a"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "b"},
            {"index": 3, "start": "00:00:03,000", "end": "00:00:04,000", "text": "c"},
            {"index": 4, "start": "00:00:04,000", "end": "00:00:05,000", "text": "d"},
        ]

        mapped = map_text_to_timed_entries("上半段\n下半段", original)

        self.assertEqual(len(mapped), 4)
        self.assertEqual(mapped[0]["start"], "00:00:01,000")
        self.assertEqual(mapped[0]["end"], "00:00:02,000")
        self.assertEqual(mapped[1]["start"], "00:00:02,000")
        self.assertEqual(mapped[-1]["end"], "00:00:05,000")
        self.assertEqual("".join(str(item["text"]) for item in mapped), "上半段下半段")

    def test_map_single_translated_line_keeps_fast_asr_timeline(self):
        """整段翻译兜底不能把开头多句字幕压成一条长字幕"""
        original = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:02,220", "text": "This is Minecraft Bedrock Edition,"},
            {"index": 2, "start": "00:00:02,420", "end": "00:00:04,220", "text": "the version of Minecraft that's available on"},
            {"index": 3, "start": "00:00:04,220", "end": "00:00:05,500", "text": "all platforms with"},
            {"index": 4, "start": "00:00:05,500", "end": "00:00:06,940", "text": "some differences to Java."},
        ]

        mapped = map_text_to_timed_entries("这是《我的世界》基岩版，一个全平台互通但和 Java 版有些不同的版本。", original)

        self.assertEqual(len(mapped), len(original))
        self.assertEqual([item["start"] for item in mapped], [item["start"] for item in original])
        self.assertEqual([item["end"] for item in mapped], [item["end"] for item in original])
        self.assertLessEqual(max(len(str(item["text"])) for item in mapped), 20)

    def test_map_single_translated_line_avoids_cjk_word_boundary_breaks(self):
        """整段翻译回填时不能把“产生”切成“产 / 生”两条字幕"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "aaaaaaaaaaaaaa"},
            {"index": 2, "start": "00:00:03,000", "end": "00:00:04,000", "text": "bbb"},
        ]

        mapped = map_text_to_timed_entries("为了让你再也无法对别的女人产生反应", original)

        self.assertEqual("".join(str(item["text"]) for item in mapped), "为了让你再也无法对别的女人产生反应")
        self.assertFalse(str(mapped[0]["text"]).endswith("产"))
        self.assertTrue(str(mapped[1]["text"]).startswith("产生"))

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
