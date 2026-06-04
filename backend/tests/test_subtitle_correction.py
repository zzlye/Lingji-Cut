# backend/tests/test_subtitle_correction.py
# 字幕校对测试 - 验证手动修正字幕的解析、保存和 ASS 生成能力

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.subtitles import (  # noqa: E402
    _default_subtitle_presets,
    ensure_default_subtitle_presets,
    SubtitleCorrectionSaveAssRequest,
    SubtitleCorrectionSaveRequest,
    SubtitleEntryPayload,
    save_corrected_ass,
    save_corrected_subtitle,
)
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


class PresetListQuery:
    """测试用预设查询对象"""

    def __init__(self, presets):
        self.presets = presets

    def first(self):
        return self.presets[0] if self.presets else None

    def all(self):
        return self.presets


class PresetListDb:
    """测试用预设数据库会话"""

    def __init__(self, presets):
        self.presets = presets
        self.commit_count = 0

    def query(self, *_):
        return PresetListQuery(self.presets)

    def commit(self):
        self.commit_count += 1


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

    def test_parse_youtube_vtt_cleans_inline_timestamps_and_rolling_duplicates(self):
        """YouTube 自动 VTT 会清理内联标签，并去掉滚动字幕重复前缀"""
        content = """WEBVTT
Kind: captions
Language: ja

00:00:00.799 --> 00:00:00.990 align:start position:0%
5

00:00:01.000 --> 00:00:11.669 align:start position:0%
5
月スタートは関東で大雨となる恐れがあります。

00:00:21.279 --> 00:00:21.750 align:start position:0%
""" + " \nはい。\n\n" + """00:00:21.760 --> 00:00:23.910 align:start position:0%
はい。
え、まずは気圧地です。<00:00:23.080><c>こちら</c><00:00:23.480><c>5</c><00:00:23.599><c>月</c><00:00:23.840><c>1</c>

00:00:23.920 --> 00:00:24.830 align:start position:0%
え、まずは気圧地です。こちら5月1
日金曜日午前<00:00:24.720><c>9</c>

00:00:24.840 --> 00:00:29.870 align:start position:0%
日金曜日午前9
時の総点記ですけども
"""

        entries = SubtitleEngine().parse_vtt_content(content)
        texts = [entry["text"] for entry in entries]

        self.assertEqual(texts[0], "5月スタートは関東で大雨となる恐れがあります。")
        self.assertEqual(texts[1], "はい。")
        self.assertEqual(texts[2], "え、まずは気圧地です。こちら5月1")
        self.assertEqual(texts[3], "日金曜日午前9")
        self.assertEqual(texts[4], "時の総点記ですけども")
        self.assertNotIn("<00:", "\n".join(texts))
        self.assertNotIn("<c>", "\n".join(texts))

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

    def test_generate_ass_single_line_does_not_insert_line_breaks(self):
        """单行字幕不会写入 ASS 换行符，避免画面出现双行字幕"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "single.ass")
            long_text = "午前中というところがメインかなと思うんですが、時々こう激しく降る時間帯があるという風に見ておいてください。"

            SubtitleEngine().generate_ass(
                [{"index": 1, "start": "00:00:01,000", "end": "00:00:05,000", "text": long_text}],
                output_path,
                {"font_size": 48, "line_mode": "single"},
            )

            with open(output_path, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertNotIn("\\N", content)
            self.assertIn("WrapStyle: 2", content)

    def test_default_subtitle_presets_are_single_line(self):
        """内置默认预设必须是单行，避免新用户一键流程生成双行字幕"""
        presets = _default_subtitle_presets()

        self.assertTrue(presets)
        self.assertTrue(all(preset.line_mode == "single" for preset in presets))

    def test_ensure_default_subtitle_presets_repairs_legacy_short_video_preset(self):
        """旧版短视频默认预设启动后会修正为单行，但不改明确双语模板"""
        short_video = SimpleNamespace(name="短视频清晰字幕", line_mode="double")
        bilingual = SimpleNamespace(name="电影双语", line_mode="double")
        db = PresetListDb([short_video, bilingual])

        ensure_default_subtitle_presets(db)

        self.assertEqual(short_video.line_mode, "single")
        self.assertEqual(bilingual.line_mode, "double")
        self.assertEqual(db.commit_count, 1)

    def test_normalize_entries_for_display_splits_long_single_line_text(self):
        """长字幕会按时间比例拆成多条短字幕，改善硬字幕时间轴"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:01,000", "end": "00:00:05,000", "text": "结合这些情况，我们来看一下未来三小时的整体模拟过程，首先是凌晨三点的降雨情况。"}],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertGreater(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:01,000")
        self.assertEqual(entries[-1]["end"], "00:00:05,000")
        self.assertTrue(all("\\N" not in entry["text"] for entry in entries))
        self.assertFalse(any(entry["text"].startswith(("，", "。")) for entry in entries))

    def test_normalize_entries_for_display_uses_text_weighted_timing(self):
        """拆分字幕按文字长度分配时长，避免短句和长句被平均切分导致时间轴偏移"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "这是一个非常长的字幕前半段内容需要完整保留，短句。"}],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0]["text"].endswith("，"))
        self.assertGreater(entries[0]["end"], "00:00:03,000")
        self.assertEqual(entries[-1]["end"], "00:00:04,000")


if __name__ == "__main__":
    unittest.main()
