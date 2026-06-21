# Gemini 音频识别测试 - 不碰真实网络和模型，验证分段、JSON 解析、时间轴对齐核心逻辑

import os
import sys
import unittest


# 嵌入式 Python 直接运行测试时需要手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.audio_transcriber import (
    GeminiAudioTranscriber,
    align_gemini_content_to_whisper_timeline,
)
from backend.core.local_asr import LocalSpeechRecognizer


def _make_transcriber() -> GeminiAudioTranscriber:
    """构造一个识别器实例，仅用于调用纯逻辑方法，不会发请求"""
    return GeminiAudioTranscriber(
        provider_type="openai_compatible",
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="gemini-2.5-pro",
        settings={},
    )


class PlanSegmentsTest(unittest.TestCase):
    """分段规划测试"""

    def test_merges_adjacent_regions_within_limit(self):
        """跨度不超过上限的相邻语音区间应合并成一段，切点落在静音处"""
        transcriber = _make_transcriber()
        regions = [(0.0, 10.0), (12.0, 20.0), (100.0, 110.0)]
        segments = transcriber._plan_segments(regions, duration=120.0, max_len=90.0)
        # 前两段合并(跨度20s)，第三段单独
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0][0], 0.0, places=1)
        self.assertTrue(19.9 <= segments[0][1] <= 20.4)
        self.assertTrue(99.6 <= segments[1][0] <= 100.0)

    def test_splits_when_exceeding_max_len(self):
        """累计跨度超过上限时在静音处断成多段"""
        transcriber = _make_transcriber()
        regions = [(0.0, 50.0), (55.0, 100.0), (105.0, 150.0)]
        segments = transcriber._plan_segments(regions, duration=160.0, max_len=90.0)
        self.assertGreaterEqual(len(segments), 2)

    def test_empty_regions_returns_empty(self):
        """没有语音区间时返回空，由上层兜底整段处理"""
        transcriber = _make_transcriber()
        self.assertEqual(transcriber._plan_segments([], duration=60.0, max_len=90.0), [])


class ParseSegmentResponseTest(unittest.TestCase):
    """模型返回解析与时间偏移测试"""

    def test_parses_json_and_offsets_to_full_timeline(self):
        """段内相对秒数解析后要加上段起点偏移，转成完整 SRT 时间码"""
        transcriber = _make_transcriber()
        text = '[{"start":0.5,"end":2.0,"text":"これ"},{"start":3.0,"end":4.5,"text":"なに？"}]'
        entries = transcriber._parse_segment_response(text, seg_start=100.0)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["start"], "00:01:40,500")
        self.assertEqual(entries[0]["end"], "00:01:42,000")
        self.assertEqual(entries[0]["text"], "これ")
        self.assertEqual(entries[1]["start"], "00:01:43,000")

    def test_handles_markdown_fence(self):
        """模型用 markdown 代码块包裹 JSON 时也能解析"""
        transcriber = _make_transcriber()
        text = '```json\n[{"start":0,"end":1,"text":"hello"}]\n```'
        entries = transcriber._parse_segment_response(text, seg_start=0.0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "hello")

    def test_handles_extra_text_around_json(self):
        """JSON 前后有多余说明文字时仍能抽出数组"""
        transcriber = _make_transcriber()
        text = '好的，转写结果如下：\n[{"start":1,"end":2,"text":"よし"}]\n以上。'
        entries = transcriber._parse_segment_response(text, seg_start=10.0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "よし")
        self.assertEqual(entries[0]["start"], "00:00:11,000")

    def test_empty_array_returns_no_entries(self):
        """模型判定无说话内容返回空数组时不产生字幕"""
        transcriber = _make_transcriber()
        self.assertEqual(transcriber._parse_segment_response("[]", seg_start=0.0), [])

    def test_skips_empty_text_items(self):
        """空文本条目被过滤"""
        transcriber = _make_transcriber()
        text = '[{"start":0,"end":1,"text":""},{"start":1,"end":2,"text":"有内容"}]'
        entries = transcriber._parse_segment_response(text, seg_start=0.0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "有内容")


class AlignTimelineTest(unittest.TestCase):
    """方案3：Gemini 内容对齐到 Whisper 时间轴测试"""

    def setUp(self):
        """准备时间码换算工具"""
        self.asr = LocalSpeechRecognizer()

    def _align(self, whisper_entries, gemini_entries):
        """调用对齐函数的便捷封装"""
        return align_gemini_content_to_whisper_timeline(
            whisper_entries,
            gemini_entries,
            self.asr._srt_time_to_seconds,
            self.asr._seconds_to_srt_time,
        )

    def test_replaces_text_keeps_whisper_timeline(self):
        """用时间重叠的 Gemini 文本替换 Whisper 文本，时间轴保持 Whisper 的精确边界"""
        whisper = [{"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "話はまずここから"}]
        gemini = [{"index": 1, "start": "00:00:01,100", "end": "00:00:02,900", "text": "話はまずそこからやないですか"}]
        out = self._align(whisper, gemini)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "話はまずそこからやないですか")
        self.assertEqual(out[0]["start"], "00:00:01,000")
        self.assertEqual(out[0]["end"], "00:00:03,000")

    def test_inserts_sentence_missed_by_whisper(self):
        """Gemini 有、Whisper 时间轴没覆盖的句子作为补漏条目插入"""
        whisper = [{"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "第一句"}]
        gemini = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "第一句"},
            {"index": 2, "start": "00:00:08,000", "end": "00:00:09,500", "text": "被漏掉的话"},
        ]
        out = self._align(whisper, gemini)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["text"], "被漏掉的话")
        self.assertEqual(out[1]["start"], "00:00:08,000")

    def test_keeps_whisper_when_gemini_empty(self):
        """Gemini 没返回内容时保留 Whisper 结果，不至于丢字幕"""
        whisper = [{"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "保留"}]
        out = self._align(whisper, [])
        self.assertEqual(out, whisper)

    def test_merges_multiple_gemini_sentences_into_one_whisper_slot(self):
        """一个较长的 Whisper 时间段覆盖多句 Gemini 时，按时间顺序拼接"""
        whisper = [{"index": 1, "start": "00:00:00,000", "end": "00:00:06,000", "text": "原文一段"}]
        gemini = [
            {"index": 1, "start": "00:00:00,500", "end": "00:00:02,000", "text": "句子A"},
            {"index": 2, "start": "00:00:03,000", "end": "00:00:05,000", "text": "句子B"},
        ]
        out = self._align(whisper, gemini)
        self.assertEqual(len(out), 1)
        self.assertIn("句子A", out[0]["text"])
        self.assertIn("句子B", out[0]["text"])

    def test_long_gemini_sentence_is_not_duplicated_across_whisper_slots(self):
        """Gemini 长句跨多个本地时间段时应切分分配，不能整句重复塞进每一条"""
        whisper = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,140", "text": "On the strongest SMP"},
            {"index": 2, "start": "00:00:01,760", "end": "00:00:03,020", "text": "Minecraft's sweatiest server"},
            {"index": 3, "start": "00:00:03,260", "end": "00:00:05,800", "text": "it is required to have a public base"},
        ]
        gemini_text = "On the strongest SMP Minecraft's sweatiest server it is required to have a public base"
        gemini = [{"index": 1, "start": "00:00:00,000", "end": "00:00:05,800", "text": gemini_text}]

        out = self._align(whisper, gemini)

        self.assertEqual(len(out), 3)
        self.assertNotEqual(out[0]["text"], gemini_text)
        self.assertNotEqual(out[1]["text"], gemini_text)
        self.assertNotEqual(out[2]["text"], gemini_text)
        self.assertEqual(" ".join(entry["text"] for entry in out), gemini_text)


class AudioContentPartTest(unittest.TestCase):
    """音频多模态片段格式测试：默认 image_url data URI，可切回 input_audio"""

    def test_default_uses_image_url_data_uri(self):
        """默认走 image_url data URI（此类中转只认这种，input_audio 会被丢弃）"""
        import os
        from unittest.mock import patch
        transcriber = _make_transcriber()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YTV_GEMINI_ASR_AUDIO_FORMAT", None)
            part = transcriber._audio_content_part("QUJD")
        self.assertEqual(part["type"], "image_url")
        self.assertTrue(part["image_url"]["url"].startswith("data:audio/mpeg;base64,"))
        self.assertIn("QUJD", part["image_url"]["url"])

    def test_env_can_switch_to_input_audio(self):
        """环境变量可切回 input_audio 格式以兼容别的中转"""
        import os
        from unittest.mock import patch
        transcriber = _make_transcriber()
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_AUDIO_FORMAT": "input_audio"}):
            part = transcriber._audio_content_part("QUJD")
        self.assertEqual(part["type"], "input_audio")
        self.assertEqual(part["input_audio"]["data"], "QUJD")


if __name__ == "__main__":
    unittest.main()
