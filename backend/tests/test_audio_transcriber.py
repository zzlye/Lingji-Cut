# Gemini 音频识别测试 - 不碰真实网络和模型，验证分段、JSON 解析、时间轴对齐核心逻辑

import os
import sys
import asyncio
import unittest
from unittest.mock import patch


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

    def test_full_coverage_segments_fill_vad_gaps(self):
        """默认覆盖完整音频，即使 VAD 没检测到的空档也会发给 Gemini"""
        transcriber = _make_transcriber()
        regions = [(0.0, 10.0), (12.0, 20.0), (100.0, 110.0)]
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "1"}):
            segments = transcriber._plan_segments(regions, duration=120.0, max_len=90.0)

        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0][0], 0.0, places=1)
        self.assertGreaterEqual(segments[0][1], 59.0)
        self.assertLessEqual(segments[1][0], segments[0][1])
        self.assertAlmostEqual(segments[-1][1], 120.0, places=1)

    def test_vad_only_segments_can_be_enabled(self):
        """需要省 token 时仍可用环境变量切回旧版只发 VAD 人声区间"""
        transcriber = _make_transcriber()
        regions = [(0.0, 10.0), (12.0, 20.0), (100.0, 110.0)]
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "0"}):
            segments = transcriber._plan_segments(regions, duration=120.0, max_len=90.0)

        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0][0], 0.0, places=1)
        self.assertTrue(19.9 <= segments[0][1] <= 20.4)
        self.assertTrue(99.6 <= segments[1][0] <= 100.0)

    def test_vad_only_long_region_is_force_split(self):
        """即使 VAD 把整片当成一个语音区间，也不能发 421 秒单段给 Gemini"""
        transcriber = _make_transcriber()
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "0"}):
            segments = transcriber._plan_segments([(0.0, 421.3)], duration=421.3, max_len=90.0)

        self.assertGreater(len(segments), 1)
        self.assertAlmostEqual(segments[0][0], 0.0, places=1)
        self.assertAlmostEqual(segments[-1][1], 421.3, places=1)
        self.assertTrue(all(end - start <= 91.0 for start, end in segments))

    def test_frontend_settings_override_hidden_environment_defaults(self):
        """界面传入的 Gemini 切片参数优先，隐藏环境变量只作为兜底"""
        transcriber = GeminiAudioTranscriber(
            provider_type="openai_compatible",
            api_key="sk-test",
            base_url="https://example.com/v1",
            model="gemini-2.5-pro",
            settings={
                "segment_seconds": 45,
                "segment_overlap_seconds": 0.7,
                "full_coverage": False,
                "audio_concurrency": 4,
                "audio_timeout_seconds": 180,
            },
        )
        with patch.dict(os.environ, {
            "YTV_GEMINI_ASR_SEGMENT_S": "180",
            "YTV_GEMINI_ASR_SEGMENT_OVERLAP_S": "0.1",
            "YTV_GEMINI_ASR_FULL_COVERAGE": "1",
            "YTV_GEMINI_ASR_CONCURRENCY": "1",
            "YTV_GEMINI_ASR_TIMEOUT": "600",
        }):
            self.assertEqual(transcriber._segment_seconds(), 45)
            self.assertEqual(transcriber._segment_overlap_seconds(), 0.7)
            self.assertFalse(transcriber._full_coverage_segments_enabled())
            self.assertEqual(transcriber._concurrency(), 4)
            self.assertEqual(transcriber._timeout(), 180)

    def test_splits_when_exceeding_max_len(self):
        """累计跨度超过上限时在静音处断成多段"""
        transcriber = _make_transcriber()
        regions = [(0.0, 50.0), (55.0, 100.0), (105.0, 150.0)]
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "1"}):
            segments = transcriber._plan_segments(regions, duration=160.0, max_len=90.0)
        self.assertGreaterEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0][0], 0.0, places=1)
        self.assertAlmostEqual(segments[-1][1], 160.0, places=1)

    def test_empty_regions_returns_whole_audio_in_full_coverage_mode(self):
        """没有 VAD 区间时仍把整段音频交给 Gemini 判断，避免细声细语被整体跳过"""
        transcriber = _make_transcriber()
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "1"}):
            self.assertEqual(transcriber._plan_segments([], duration=60.0, max_len=90.0), [(0.0, 60.0)])

    def test_empty_regions_can_return_empty_in_vad_only_mode(self):
        """旧版 VAD-only 模式没有语音区间时仍由上层兜底整段处理"""
        transcriber = _make_transcriber()
        with patch.dict(os.environ, {"YTV_GEMINI_ASR_FULL_COVERAGE": "0"}):
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


class GeminiSegmentFailureTest(unittest.TestCase):
    """Gemini 分段失败不能静默漏字幕"""

    def test_invalid_non_empty_response_raises_for_retry(self):
        """模型返回非空乱码时不能当作无字幕片段吞掉"""
        transcriber = _make_transcriber()

        async def fake_call(_client, _audio_b64, _language):
            return "无法整理成 JSON"

        with patch.object(transcriber, "_call_audio_once", side_effect=fake_call):
            with self.assertRaises(RuntimeError) as context:
                asyncio.run(transcriber._transcribe_one_segment(object(), 0.0, __file__, None))

        self.assertIn("可解析的字幕 JSON", str(context.exception))

    def test_segment_request_failure_stops_whole_transcription(self):
        """任一音频分段失败时整体失败，避免显示成功但实际漏一段"""
        transcriber = _make_transcriber()

        async def fake_transcribe(_client, seg_start, _path, _language):
            if seg_start == 0.0:
                raise RuntimeError("接口超时")
            return [{"index": 1, "start": "00:00:10,000", "end": "00:00:11,000", "text": "后半段"}]

        with patch.object(transcriber, "_transcribe_one_segment", side_effect=fake_transcribe):
            with self.assertRaises(RuntimeError) as context:
                asyncio.run(transcriber._transcribe_segments(
                    [(0, 0.0, __file__), (1, 10.0, __file__)],
                    None,
                    None,
                ))

        self.assertIn("已停止避免漏字幕", str(context.exception))


class AudioHttpErrorTest(unittest.TestCase):
    """音频接口错误提示测试"""

    def test_openai_audio_524_reports_gateway_timeout_without_html_dump(self):
        """中转返回 524 HTML 时，应提示超时而不是把整页 HTML 打到前端"""
        transcriber = _make_transcriber()

        class FakeResponse:
            status_code = 524
            text = "<!DOCTYPE html><html><head><title>524 A timeout occurred</title></head><body>cloudflare</body></html>"

            def json(self):
                raise ValueError("not json")

        class FakeClient:
            async def post(self, *_args, **_kwargs):
                return FakeResponse()

        with self.assertRaises(RuntimeError) as context:
            asyncio.run(transcriber._call_openai_audio(FakeClient(), "abc", "prompt"))

        message = str(context.exception)
        self.assertIn("HTTP 524", message)
        self.assertIn("中转服务超时", message)
        self.assertIn("524 A timeout occurred", message)
        self.assertNotIn("<!DOCTYPE", message)


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

    def test_long_gemini_sentence_uses_local_speech_density_when_split(self):
        """同样时长的本地时间槽说话密度不同，Gemini 文本应按本地识别密度拆分，避免节奏漂移"""
        whisper = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "one two three four five six seven eight"},
            {"index": 2, "start": "00:00:04,000", "end": "00:00:08,000", "text": "pause"},
        ]
        gemini = [{
            "index": 1,
            "start": "00:00:00,000",
            "end": "00:00:08,000",
            "text": "alpha beta gamma delta epsilon zeta eta theta iota",
        }]

        out = self._align(whisper, gemini)

        self.assertEqual(out[0]["text"], "alpha beta gamma delta epsilon zeta eta theta")
        self.assertEqual(out[1]["text"], "iota")

    def test_missing_gemini_sentences_do_not_create_dense_flash_subtitles(self):
        """Gemini 异常把多句补漏挤到同一瞬间时，应合并到真实空档而不是生成密集闪字幕"""
        whisper = [
            {"index": 1, "start": "00:01:42,000", "end": "00:01:43,000", "text": "before"},
            {"index": 2, "start": "00:01:48,880", "end": "00:01:51,470", "text": "after"},
        ]
        gemini = [
            {"index": 1, "start": "00:01:42,000", "end": "00:01:43,000", "text": "before"},
            {"index": 2, "start": "00:01:44,024", "end": "00:01:44,224", "text": "If I build a section above ground"},
            {"index": 3, "start": "00:01:44,054", "end": "00:01:44,254", "text": "players will attack the decoy base"},
            {"index": 4, "start": "00:01:44,134", "end": "00:01:44,334", "text": "the real one stays hidden underground"},
            {"index": 5, "start": "00:01:44,234", "end": "00:01:44,434", "text": "Yo we're not even ready"},
            {"index": 6, "start": "00:01:44,254", "end": "00:01:44,454", "text": "Falcon's not here"},
            {"index": 7, "start": "00:01:48,880", "end": "00:01:51,470", "text": "after"},
        ]

        out = self._align(whisper, gemini)
        inserted = [
            entry for entry in out
            if "before" not in entry["text"] and "after" not in entry["text"]
        ]

        self.assertLessEqual(len(inserted), 6)
        for entry in inserted:
            start = self.asr._srt_time_to_seconds(entry["start"])
            end = self.asr._srt_time_to_seconds(entry["end"])
            self.assertGreaterEqual(end - start, 0.75)
        for left, right in zip(inserted, inserted[1:]):
            self.assertLessEqual(
                self.asr._srt_time_to_seconds(left["end"]),
                self.asr._srt_time_to_seconds(right["start"]),
            )


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


class DedupeBoundaryRepeatsTest(unittest.TestCase):
    """相邻分段边界重复转写去重测试"""

    def test_drops_entry_fully_repeated_in_previous(self):
        """后一条整句是前一条的连续子串时应整条丢弃"""
        transcriber = _make_transcriber()
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:03,000", "text": "they thought defeating me would be the end"},
            {"index": 2, "start": "00:00:02,800", "end": "00:00:04,000", "text": "defeating me would be the end"},
        ]
        out = transcriber._dedupe_boundary_repeats(entries)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "they thought defeating me would be the end")

    def test_strips_overlap_at_boundary(self):
        """前一条结尾和后一条开头的连续重复词应从后一条裁掉"""
        transcriber = _make_transcriber()
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:03,000", "text": "I joined in with a plan to make"},
            {"index": 2, "start": "00:00:02,900", "end": "00:00:05,000", "text": "with a plan to make the best base ever"},
        ]
        out = transcriber._dedupe_boundary_repeats(entries)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["text"], "the best base ever")

    def test_keeps_normal_short_repeats(self):
        """正常的短重复（No No）不应被误删"""
        transcriber = _make_transcriber()
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "No"},
            {"index": 2, "start": "00:00:01,200", "end": "00:00:02,000", "text": "No"},
        ]
        out = transcriber._dedupe_boundary_repeats(entries)
        self.assertEqual(len(out), 2)

    def test_keeps_distant_same_text(self):
        """时间相隔很远的相同句子（真的说了两遍）不去重"""
        transcriber = _make_transcriber()
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:03,000", "text": "this marks the beginning of the war"},
            {"index": 2, "start": "00:05:00,000", "end": "00:05:03,000", "text": "this marks the beginning of the war"},
        ]
        out = transcriber._dedupe_boundary_repeats(entries)
        self.assertEqual(len(out), 2)


class AlignSequenceTest(unittest.TestCase):
    """序列对齐：Gemini 词按文本对应关系落到正确的时间槽，不产生闪现/错位/重复"""

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

    def test_short_phrase_not_split_into_flash_fragments(self):
        """一句话即使本地切成多碎片，Gemini 同句词应按对应关系分配，不出现 0.2s 闪现碎片"""
        # 模拟原 146-148 条：Blow one man army baby 被本地切成 3 条短碎片
        whisper = [
            {"index": 1, "start": "00:00:35,190", "end": "00:00:36,090", "text": "Blow one"},
            {"index": 2, "start": "00:00:36,270", "end": "00:00:36,470", "text": "man army"},
            {"index": 3, "start": "00:00:36,750", "end": "00:00:36,950", "text": "baby"},
        ]
        gemini = [{"index": 1, "start": "00:00:35,100", "end": "00:00:37,000", "text": "Blow one man army baby"}]
        out = self._align(whisper, gemini)
        # 时间轴沿用本地的 3 条，内容用 Gemini 词精确落位，所有词都在、不重复
        joined = " ".join(entry["text"] for entry in out)
        self.assertEqual(joined.split(), ["Blow", "one", "man", "army", "baby"])
        # 没有任何一条把整句重复塞进去
        for entry in out:
            self.assertLess(len(entry["text"].split()), 5)

    def test_no_time_overlap_between_entries(self):
        """对齐结果相邻条目时间不重叠、不倒退"""
        whisper = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:02,000", "text": "hello there"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:04,000", "text": "how are you"},
        ]
        gemini = [{"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "hello there how are you"}]
        out = self._align(whisper, gemini)
        for left, right in zip(out, out[1:]):
            self.assertLessEqual(
                self.asr._srt_time_to_seconds(left["end"]),
                self.asr._srt_time_to_seconds(right["start"]) + 0.001,
            )

    def test_gemini_word_corrects_whisper_misheard(self):
        """Whisper 听错的词被 Gemini 正确词替换，时间轴保持本地精确边界"""
        whisper = [{"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "I built the base war"}]
        gemini = [{"index": 1, "start": "00:00:01,050", "end": "00:00:02,950", "text": "I built the best base"}]
        out = self._align(whisper, gemini)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "I built the best base")
        self.assertEqual(out[0]["start"], "00:00:01,000")
        self.assertEqual(out[0]["end"], "00:00:03,000")

    def test_entry_without_gemini_match_keeps_whisper_text(self):
        """分不到任何 Gemini 词的条目保留原 Whisper 文本兜底，不留空"""
        whisper = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "matched words here"},
            {"index": 2, "start": "00:01:00,000", "end": "00:01:01,000", "text": "lonely whisper line"},
        ]
        gemini = [{"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "matched words here"}]
        out = self._align(whisper, gemini)
        texts = [entry["text"] for entry in out]
        self.assertIn("lonely whisper line", texts)
        for entry in out:
            self.assertTrue(entry["text"].strip())


class LocalAsrWorkerLaunchTest(unittest.TestCase):
    """本地 ASR CUDA worker 启动命令测试"""

    def test_cuda_worker_uses_script_path_for_embedded_python(self):
        """CUDA 子进程直接执行 worker 脚本，避免嵌入式 Python 找不到 backend 包"""
        captured: dict[str, object] = {}

        class FakeStdout:
            """模拟子进程 stdout，返回一次成功事件"""

            def __iter__(self):
                payload = '{"type":"result","language":"zh","entries":[{"text":"测试","start":"00:00:00,000","end":"00:00:01,000"}]}'
                return iter([f"__YTV_ASR_WORKER__{payload}\n"])

        class FakeProcess:
            """模拟成功退出的 CUDA worker 子进程"""

            stdout = FakeStdout()

            def wait(self):
                return 0

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakeProcess()

        recognizer = LocalSpeechRecognizer(model_name="base", device="cuda", compute_type="float16")
        with patch("backend.core.local_asr.subprocess.Popen", side_effect=fake_popen):
            entries, language = recognizer._transcribe_video_in_worker("D:\\tmp\\sample.mp4", None, None)

        command = captured["command"]
        self.assertIsInstance(command, list)
        self.assertNotIn("-m", command)
        self.assertTrue(str(command[1]).endswith(os.path.join("backend", "core", "local_asr_worker.py")))
        self.assertEqual(entries[0]["text"], "测试")
        self.assertEqual(language, "zh")


if __name__ == "__main__":
    unittest.main()
