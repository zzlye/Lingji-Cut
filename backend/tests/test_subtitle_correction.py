# backend/tests/test_subtitle_correction.py
# 字幕校对测试 - 验证手动修正字幕的解析、保存和 ASS 生成能力

import asyncio
import os
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.subtitles import (  # noqa: E402
    _default_subtitle_presets,
    ensure_default_subtitle_presets,
    SubtitleCorrectionSaveAssRequest,
    SubtitleCorrectionSaveRequest,
    SubtitleEntriesProcessRequest,
    SubtitleEntryPayload,
    process_subtitle_entries,
    save_corrected_ass,
    save_corrected_subtitle,
)
from backend.core.subtitle_engine import SubtitleEngine  # noqa: E402
from backend.models import TextProviderProfile  # noqa: E402
from backend.utils import encrypt_api_key  # noqa: E402


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


class ProfileQuery:
    """测试用文本配置查询对象"""

    def __init__(self, profile):
        self.profile = profile

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.profile


class TextProfileDb:
    """测试用文本配置数据库会话"""

    def __init__(self, profile):
        self.profile = profile

    def query(self, model):
        if model is TextProviderProfile:
            return ProfileQuery(self.profile)
        return ProfileQuery(None)


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

    def test_save_ass_without_output_path_uses_video_workspace_output_dir(self):
        """未手填输出路径时，ASS 默认保存到当前视频工作目录的 output 里"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = os.path.join(temp_dir, "videos", "demo__测试视频")
            downloads_dir = os.path.join(workspace_dir, "downloads")
            output_dir = os.path.join(workspace_dir, "output")
            os.makedirs(downloads_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)
            source_video_path = os.path.join(downloads_dir, "source.mp4")
            with open(source_video_path, "wb") as file:
                file.write(b"video")

            request = SubtitleCorrectionSaveAssRequest(
                source_path=source_video_path,
                file_name="fixed.ass",
                entries=[
                    SubtitleEntryPayload(index=1, start="00:00:01,000", end="00:00:02,000", text="工作目录字幕"),
                ],
            )

            response = asyncio.run(save_corrected_ass(request, EmptyDb()))

            self.assertEqual(response.output_path, os.path.join(output_dir, "fixed.ass"))
            self.assertTrue(os.path.exists(response.output_path))

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

    def test_parse_ass_file_restores_editable_entries(self):
        """ASS 字幕可重新解析成可编辑条目，方便主界面继续校对"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "generated.ass")
            SubtitleEngine().generate_ass(
                [{"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "主字幕\n副字幕"}],
                output_path,
                {"font_size": 48, "secondary_font_size": 32, "line_mode": "double"},
            )

            entries = SubtitleEngine().parse_ass(output_path)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["start"], "00:00:01,000")
            self.assertEqual(entries[0]["end"], "00:00:03,000")
            self.assertEqual(entries[0]["text"], "主字幕\n副字幕")

    def test_default_subtitle_presets_are_single_line(self):
        """内置默认预设必须是单行，避免新用户一键流程生成双行字幕"""
        presets = _default_subtitle_presets()

        self.assertTrue(presets)
        self.assertTrue(all(preset.line_mode == "single" for preset in presets))

    def test_ensure_default_subtitle_presets_preserves_existing_line_mode(self):
        """已有预设不再按名称强制改成单行，避免覆盖用户保存的双行选择"""
        short_video = SimpleNamespace(name="短视频清晰字幕", line_mode="double")
        bilingual = SimpleNamespace(name="电影双语", line_mode="double")
        db = PresetListDb([short_video, bilingual])

        ensure_default_subtitle_presets(db)

        self.assertEqual(short_video.line_mode, "double")
        self.assertEqual(bilingual.line_mode, "double")
        self.assertEqual(db.commit_count, 0)

    def test_generate_ass_double_line_uses_secondary_font_size(self):
        """双行字幕第二行使用独立样式和字号"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "double.ass")

            SubtitleEngine().generate_ass(
                [{"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "主字幕\n副字幕"}],
                output_path,
                {"font_size": 52, "secondary_font_size": 32, "line_mode": "double", "secondary_color": "#FDE68A"},
            )

            with open(output_path, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("WrapStyle: 0", content)
            self.assertIn("Style: Secondary,", content)
            self.assertIn("Secondary,Microsoft YaHei,32,", content)
            self.assertIn("主字幕\\N{\\rSecondary}副字幕", content)

    def test_double_line_display_entries_keep_original_timing(self):
        """双行模式不使用单行拆分逻辑，避免一条双语字幕被拆成多个时间段"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:01,000", "end": "00:00:05,000", "text": "原文第一行\nTranslated second line"}],
            {"font_size": 48, "line_mode": "double"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:01,000")
        self.assertEqual(entries[0]["end"], "00:00:05,000")
        self.assertEqual(entries[0]["text"], "原文第一行\nTranslated second line")

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
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "这是一个非常长的字幕前半段内容需要完整保留；短句还需要继续补充说明"}],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0]["text"].endswith("；"))
        self.assertGreater(entries[0]["end"], "00:00:02,500")
        self.assertEqual(entries[-1]["end"], "00:00:04,000")

    def test_split_subtitle_text_does_not_leave_punctuation_only_or_single_char_fragments(self):
        """字幕切分不能留下纯标点条目，也不能把最后一个有效字单独切成一条"""
        parts = SubtitleEngine()._split_subtitle_text(
            "这是一段比较长的字幕内容，需要继续说明......最后不会单独剩下啊。",
            12,
        )

        self.assertGreater(len(parts), 1)
        self.assertFalse(any(re.fullmatch(r"[\s，。、！？；：,.!?;:…]+", part) for part in parts))
        self.assertFalse(any(len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", part)) == 1 for part in parts))

    def test_normalize_entries_for_display_drops_punctuation_only_entries(self):
        """显示字幕会丢弃只有逗号、句号或省略号的无意义条目"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [
                {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "，"},
                {"index": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "..."},
                {"index": 3, "start": "00:00:02,000", "end": "00:00:03,000", "text": "有效字幕"},
            ],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertEqual([entry["text"] for entry in entries], ["有效字幕"])

    def test_output_subtitle_text_removes_comma_period_and_ellipsis(self):
        """输出字幕正文会移除逗号、句号和省略号"""
        engine = SubtitleEngine()

        text = engine.clean_subtitle_text_for_output("Hello, world...\\N这是中文，保留文字。")

        self.assertEqual(text, "Hello world\n这是中文保留文字")
        self.assertNotRegex(text, r"[，。,.]|\.{3,}|…")

    def test_process_subtitle_entries_keeps_original_timeline(self):
        """字幕条目 AI 处理接口返回后仍保持原始时间轴"""
        profile = SimpleNamespace(
            id=1,
            provider_type="openai",
            api_key_encrypted=encrypt_api_key("sk-test"),
            base_url="https://example.com/v1",
            model="gpt-4.1-mini",
            extra_params='{"subtitle_batch_size": 12}',
        )
        db = TextProfileDb(profile)
        request = SubtitleEntriesProcessRequest(
            profile_id=1,
            operation="translate",
            target_language="zh-CN",
            entries=[
                SubtitleEntryPayload(index=1, start="00:00:01,000", end="00:00:02,500", text="hello"),
                SubtitleEntryPayload(index=2, start="00:00:02,500", end="00:00:04,000", text="world"),
            ],
        )

        with patch("backend.api.subtitles.TextEngine.process_subtitle_entries", new=AsyncMock(return_value=[
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,500", "text": "你好"},
            {"index": 2, "start": "00:00:02,500", "end": "00:00:04,000", "text": "世界"},
        ])):
            response = asyncio.run(process_subtitle_entries(request, db))

        self.assertEqual(response.operation, "translate")
        self.assertEqual(response.entries[0].start, "00:00:01,000")
        self.assertEqual(response.entries[1].end, "00:00:04,000")
        self.assertEqual([entry.text for entry in response.entries], ["你好", "世界"])


if __name__ == "__main__":
    unittest.main()
