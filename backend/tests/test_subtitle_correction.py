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
    _parse_subtitle_entries,
    ensure_default_subtitle_presets,
    rename_preset,
    SubtitleCorrectionSaveAssRequest,
    SubtitleCorrectionSaveRequest,
    SubtitleEntriesProcessRequest,
    SubtitleEntryPayload,
    SubtitleSegmentRecognizeRequest,
    SubtitlePresetRename,
    process_subtitle_entries,
    recognize_subtitle_segment,
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

    def filter(self, *_args, **_kwargs):
        return self

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

    def refresh(self, _item):
        return None


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

    def test_save_srt_removes_nearby_duplicate_entries_on_disk(self):
        """保存字幕时只移除紧邻重复残留，隔开复读的同一句话要保留"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "deduped.srt")
            request = SubtitleCorrectionSaveRequest(
                output_path=output_path,
                entries=[
                    SubtitleEntryPayload(index=1, start="00:00:01,000", end="00:00:02,000", text="重复字幕"),
                    SubtitleEntryPayload(index=2, start="00:00:02,100", end="00:00:03,000", text="重复字幕"),
                    SubtitleEntryPayload(index=3, start="00:00:05,000", end="00:00:06,000", text="不同字幕"),
                    SubtitleEntryPayload(index=4, start="00:00:08,000", end="00:00:09,000", text="重复字幕"),
                ],
            )

            response = asyncio.run(save_corrected_subtitle(request))
            reparsed = SubtitleEngine().parse_srt(response.output_path)

            self.assertEqual([entry["text"] for entry in reparsed], ["重复字幕", "不同字幕", "重复字幕"])
            self.assertEqual([entry["start"] for entry in reparsed], ["00:00:01,000", "00:00:05,000", "00:00:08,000"])
            self.assertEqual(SubtitleEngine().duplicate_text_count(reparsed), 0)

    def test_parse_subtitle_entries_deduplicates_nearby_old_file_residue(self):
        """加载旧字幕文件时清理紧邻重复残留，但不能删掉后面正常复读"""
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle_path = os.path.join(temp_dir, "old.srt")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write(
                    "1\n00:00:01,000 --> 00:00:02,000\n重复字幕\n\n"
                    "2\n00:00:02,100 --> 00:00:03,000\n重复字幕\n\n"
                    "3\n00:00:05,000 --> 00:00:06,000\n不同字幕\n\n"
                    "4\n00:00:08,000 --> 00:00:09,000\n重复字幕\n"
                )

            entries = _parse_subtitle_entries(SubtitleEngine(), subtitle_path)

            self.assertEqual([entry["text"] for entry in entries], ["重复字幕", "不同字幕", "重复字幕"])
            self.assertEqual([entry["start"] for entry in entries], ["00:00:01,000", "00:00:05,000", "00:00:08,000"])
            self.assertEqual(SubtitleEngine().duplicate_text_count(entries), 0)

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

    def test_generate_ass_uses_actual_video_resolution_for_position(self):
        """ASS 画布跟随实际视频分辨率，避免字幕位置按固定 1080p 偏移"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "position.ass")

            SubtitleEngine().generate_ass(
                [{"index": 1, "start": "00:00:01,000", "end": "00:00:05,000", "text": "测试字幕"}],
                output_path,
                {"font_size": 48, "line_mode": "single"},
                video_size=(320, 180),
            )

            with open(output_path, "r", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("PlayResX: 320", content)
            self.assertIn("PlayResY: 180", content)

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

    def test_rename_preset_only_changes_name(self):
        """字幕预设改名不能改动其它样式参数"""
        preset = SimpleNamespace(id=1, name="旧名称", line_mode="double", font_size=80)
        db = PresetListDb([preset])

        result = asyncio.run(rename_preset(1, SubtitlePresetRename(name="新名称"), db))

        self.assertEqual(result.name, "新名称")
        self.assertEqual(preset.line_mode, "double")
        self.assertEqual(preset.font_size, 80)
        self.assertEqual(db.commit_count, 1)

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

    def test_normalize_entries_for_display_keeps_regular_single_line_text(self):
        """普通短字幕不应因为字号较大被硬拆成多条"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,360", "text": "在 Strength SMP 上"}],
            {"font_size": 80, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "在 Strength SMP 上")
        self.assertEqual(entries[0]["start"], "00:00:00,000")
        self.assertEqual(entries[0]["end"], "00:00:01,360")

    def test_normalize_entries_for_display_keeps_extremely_long_single_line_timing(self):
        """单行模式不再按字数二次切时间轴，避免内容被机械断开"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:01,000", "end": "00:00:07,000", "text": "结合这些情况，我们来看一下未来三小时的整体模拟过程，首先是凌晨三点的降雨情况，随后雨带会继续向东移动并逐渐减弱。"}],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:01,000")
        self.assertEqual(entries[0]["end"], "00:00:07,000")
        self.assertTrue(all("\\N" not in entry["text"] for entry in entries))
        self.assertFalse(any(entry["text"].startswith(("，", "。")) for entry in entries))

    def test_normalize_entries_for_display_preserves_ai_or_asr_timing(self):
        """字幕显示清理不再重新分配时间，时间轴交给 ASR 或 AI 整理结果负责"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:06,000", "text": "这是一个非常长的字幕前半段内容需要完整保留；短句还需要继续补充说明并且不能过早消失"}],
            {"font_size": 48, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:00,000")
        self.assertEqual(entries[0]["end"], "00:00:06,000")
        self.assertIn("短句还需要继续补充说明", entries[0]["text"])

    def test_normalize_entries_for_display_pulls_short_leading_phrase_to_previous_entry(self):
        """相邻字幕被切成“最好的 / 基地”时，把短词并回上一条并同步移动时间边界"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [
                {"index": 11, "start": "00:00:19,100", "end": "00:00:22,380", "text": "我带着一个计划加入其中 那就是建造一个最好的"},
                {"index": 12, "start": "00:00:22,380", "end": "00:00:25,300", "text": "基地 同时还要让它对服务器上的其他人"},
                {"index": 13, "start": "00:00:25,300", "end": "00:00:26,640", "text": "完全隐蔽"},
            ],
            {"font_size": 80, "line_mode": "single"},
        )

        self.assertEqual(entries[0]["text"], "我带着一个计划加入其中 那就是建造一个最好的基地")
        self.assertEqual(entries[1]["text"], "同时还要让它对服务器上的其他人")
        self.assertGreater(entries[0]["end"], "00:00:22,380")
        self.assertEqual(entries[0]["end"], entries[1]["start"])

    def test_normalize_entries_for_display_does_not_merge_new_subject_clause(self):
        """下一条是新主语句时不能为了凑完整把两句硬合并"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [
                {"index": 4, "start": "00:00:06,020", "end": "00:00:07,560", "text": "但只要我建好一个"},
                {"index": 5, "start": "00:00:07,720", "end": "00:00:08,580", "text": "它就会被抄家"},
            ],
            {"font_size": 80, "line_mode": "single"},
        )

        self.assertEqual([entry["text"] for entry in entries], ["但只要我建好一个", "它就会被抄家"])

    def test_normalize_entries_for_display_caps_short_single_line_duration(self):
        """短字幕不会因为识别段过长而在画面上挂很多秒"""
        entries = SubtitleEngine().normalize_entries_for_display(
            [{"index": 1, "start": "00:01:03,290", "end": "00:01:11,320", "text": "今天尝试了御姐风穿搭哦"}],
            {"font_size": 80, "line_mode": "single"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:01:03,290")
        self.assertLess(entries[0]["end"], "00:01:07,000")

    def test_split_subtitle_text_does_not_leave_punctuation_only_or_single_char_fragments(self):
        """字幕切分不能留下纯标点条目，也不能把最后一个有效字单独切成一条"""
        parts = SubtitleEngine()._split_subtitle_text(
            "这是一段比较长的字幕内容，需要继续说明......最后不会单独剩下啊。",
            12,
        )

        self.assertGreater(len(parts), 1)
        self.assertFalse(any(re.fullmatch(r"[\s，。、！？；：,.!?;:…]+", part) for part in parts))
        self.assertFalse(any(len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", part)) == 1 for part in parts))

    def test_split_subtitle_text_avoids_common_chinese_word_breaks(self):
        """字幕显示拆分不能把常见中文词切成“产 / 生”这种断词"""
        parts = SubtitleEngine()._split_subtitle_text(
            "为了让你再也无法对别的女人产生反应",
            14,
        )

        self.assertGreater(len(parts), 1)
        self.assertFalse(any(part.endswith("产") for part in parts[:-1]))
        self.assertTrue(any(part.startswith("产生") or "产生" in part for part in parts))

    def test_split_subtitle_text_keeps_short_keyword_with_previous_line(self):
        """自然断点就在后面几个字时，短核心词不要被挤到下一行开头"""
        parts = SubtitleEngine()._split_subtitle_text(
            "计划建一个最好的基地 同时还要让它完全隐蔽",
            9,
        )

        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0], "计划建一个最好的基地")
        self.assertFalse(parts[1].startswith("基地"))

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

    def test_output_entries_only_deduplicates_nearby_repeated_text(self):
        """只去掉时间紧邻的重复残留，保留后面正常复读的同一句话"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "重复字幕"},
            {"index": 2, "start": "00:00:01,100", "end": "00:00:02,000", "text": "重复字幕"},
            {"index": 3, "start": "00:00:04,000", "end": "00:00:05,000", "text": "重复字幕"},
            {"index": 4, "start": "00:00:06,000", "end": "00:00:07,000", "text": "不同字幕"},
            {"index": 5, "start": "00:00:08,000", "end": "00:00:09,000", "text": "重复字幕"},
        ]

        deduped = SubtitleEngine().dedupe_entries_by_text(entries)

        self.assertEqual([entry["text"] for entry in deduped], ["重复字幕", "重复字幕", "不同字幕", "重复字幕"])
        self.assertEqual([entry["start"] for entry in deduped], ["00:00:00,000", "00:00:04,000", "00:00:06,000", "00:00:08,000"])

    def test_duplicate_text_count_only_counts_nearby_repeated_text(self):
        """重复检测不能把隔很久再次出现的台词当成错误"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "重复字幕"},
            {"index": 2, "start": "00:00:01,100", "end": "00:00:02,000", "text": "重复字幕"},
            {"index": 3, "start": "00:00:04,000", "end": "00:00:05,000", "text": "重复字幕"},
        ]

        self.assertEqual(SubtitleEngine().duplicate_text_count(entries), 1)

    def test_double_line_deduplicates_by_primary_subtitle_line(self):
        """双语字幕按第一行主字幕去掉紧邻重复，避免滚动残留重复显示"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "重复字幕\nfirst source"},
            {"index": 2, "start": "00:00:01,100", "end": "00:00:02,000", "text": "重复字幕\nsecond source"},
        ]

        deduped = SubtitleEngine().dedupe_entries_by_text(entries)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["text"], "重复字幕\nfirst source")

    def test_output_subtitle_text_replaces_removed_punctuation_with_spacing(self):
        """输出字幕正文会把逗号、句号和省略号转成分隔空格"""
        engine = SubtitleEngine()

        text = engine.clean_subtitle_text_for_output("Hello, world...\\N这是中文，保留、文字。")

        self.assertEqual(text, "Hello world\n这是中文 保留 文字")
        self.assertNotRegex(text, r"[，。、,.]|\.{3,}|…")

    def test_output_subtitle_text_removes_cjk_digit_spaces(self):
        """中文和数字之间不应保留 AI 或识别误加的空格"""
        engine = SubtitleEngine()

        text = engine.clean_subtitle_text_for_output("哟这一下就有 80 点血了！")

        self.assertEqual(text, "哟这一下就有80点血了！")

    def test_process_subtitle_entries_keeps_original_timeline(self):
        """字幕条目 AI 处理接口返回后仍保持原始时间轴"""
        profile = SimpleNamespace(
            id=1,
            provider_type="openai",
            api_key_encrypted=encrypt_api_key("sk-test"),
            base_url="https://example.com/v1",
            model="gpt-4.1-mini",
            extra_params='{"subtitle_batch_size": 12, "system_prompt": "旧提示词"}',
        )
        db = TextProfileDb(profile)
        request = SubtitleEntriesProcessRequest(
            profile_id=1,
            operation="translate",
            target_language="zh-CN",
            custom_instruction="保留游戏术语",
            system_prompt="独立提示词预设",
            entries=[
                SubtitleEntryPayload(index=1, start="00:00:01,000", end="00:00:02,500", text="hello"),
                SubtitleEntryPayload(index=2, start="00:00:02,500", end="00:00:04,000", text="world"),
            ],
        )

        process_mock = AsyncMock(return_value=[
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,500", "text": "你好"},
            {"index": 2, "start": "00:00:02,500", "end": "00:00:04,000", "text": "世界"},
        ])
        with patch("backend.api.subtitles.TextEngine.process_subtitle_entries", new=process_mock):
            response = asyncio.run(process_subtitle_entries(request, db))

        self.assertEqual(response.operation, "translate")
        self.assertEqual(response.entries[0].start, "00:00:01,000")
        self.assertEqual(response.entries[1].end, "00:00:04,000")
        self.assertEqual([entry.text for entry in response.entries], ["你好", "世界"])
        self.assertEqual(process_mock.call_args.kwargs["custom_instruction"], "保留游戏术语")
        self.assertEqual(process_mock.call_args.kwargs["settings"]["system_prompt"], "独立提示词预设")
        self.assertEqual(process_mock.call_args.kwargs["settings"]["subtitle_batch_size"], 12)

    def test_recognize_segment_shifts_entries_to_original_timeline(self):
        """局部重新识别返回的字幕应平移回原视频时间轴"""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as file:
            file.write(b"video")
            video_path = file.name
        self.addCleanup(lambda: os.path.exists(video_path) and os.remove(video_path))
        request = SubtitleSegmentRecognizeRequest(
            video_path=video_path,
            start="00:00:10,000",
            end="00:00:15,000",
        )

        with patch("backend.api.subtitles._export_video_segment", return_value=video_path), \
                patch("backend.api.subtitles._safe_remove_file"), \
                patch("backend.api.subtitles.LocalSpeechRecognizer") as recognizer_cls:
            recognizer_cls.return_value.transcribe_video.return_value = ([
                {"index": 1, "start": "00:00:00,500", "end": "00:00:02,000", "text": "重新识别字幕"},
            ], "zh")
            response = asyncio.run(recognize_subtitle_segment(request))

        self.assertEqual(response.entries[0].start, "00:00:10,500")
        self.assertEqual(response.entries[0].end, "00:00:12,000")
        self.assertEqual(response.entries[0].text, "重新识别字幕")
        self.assertEqual(response.language, "zh")


if __name__ == "__main__":
    unittest.main()
