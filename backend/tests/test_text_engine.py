# backend/tests/test_text_engine.py
# 文本引擎测试 - 验证字幕逐条处理、解析和重试，不调用真实外部 API

import asyncio
import os
import sys
import unittest


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.text_engine import TextEngine  # noqa: E402


class FakeTextEngine(TextEngine):
    """测试用文本引擎，用固定响应代替真实 API"""

    def __init__(self, responses):
        """保存响应队列和调用次数"""
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    async def _call_prompt_once(self, *args, **kwargs):
        """返回预设响应，Exception 用于模拟失败"""
        self.calls += 1
        self.prompts.append(str(args[0] if args else kwargs.get("prompt", "")))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TextEngineTests(unittest.TestCase):
    """文本引擎单元测试"""

    def test_process_subtitle_entries_preserves_timeline_from_json(self):
        """JSON 返回会按 id 合并回原字幕时间轴"""
        engine = FakeTextEngine([
            '[{"id":1,"text":"你好世界"},{"id":2,"text":"第二句"}]',
        ])
        entries = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "hello world"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "second line"},
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={"subtitle_batch_size": 10, "retry_count": 0},
            operation="translate",
            target_language="中文",
        ))

        self.assertEqual(result[0]["text"], "你好世界")
        self.assertEqual(result[0]["start"], "00:00:01,000")
        self.assertEqual(result[1]["text"], "第二句")
        self.assertEqual(result[1]["end"], "00:00:03,000")

    def test_process_subtitle_entries_parses_loose_json_without_commas(self):
        """模型漏掉 JSON 逗号时尽量按 id/text 修复，避免直接进入整段粗切"""
        engine = FakeTextEngine([
            '[{"id":1 "text":"第一条"} {"id":2 "text":"第二条"}]',
        ])
        entries = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "first"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "second"},
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={"subtitle_batch_size": 10, "retry_count": 0},
            operation="translate",
            target_language="中文",
        ))

        self.assertEqual([entry["text"] for entry in result], ["第一条", "第二条"])

    def test_translate_bad_structured_json_does_not_pollute_subtitles(self):
        """坏 JSON 不能当普通文本分配回字幕，否则会把 id/text 残片烧进画面"""
        engine = FakeTextEngine([
            '[{"id":1 "text":"第一条"',
        ])
        entries = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "first"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "second"},
        ]

        with self.assertRaisesRegex(RuntimeError, "兜底"):
            asyncio.run(engine.process_subtitle_entries(
                entries=entries,
                provider_type="openai",
                api_key="test",
                base_url="https://example.com/v1",
                model="model",
                settings={"subtitle_batch_size": 10, "retry_count": 0},
                operation="translate",
                target_language="中文",
            ))

    def test_translate_prompt_defaults_to_simplified_chinese(self):
        """翻译未选择输出语言时默认翻译成简体中文"""
        engine = TextEngine()

        prompt = engine._build_subtitle_entries_prompt(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "テスト"}],
            operation="translate",
            target_language="",
            settings={},
        )

        self.assertIn("翻译成简体中文", prompt)
        self.assertNotIn("目标语言", prompt)

    def test_custom_instruction_is_in_subtitle_prompt(self):
        """弹窗填写的处理要求会进入字幕批处理提示词"""
        engine = TextEngine()

        prompt = engine._build_subtitle_entries_prompt(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "hello"}],
            operation="translate",
            target_language="zh-CN",
            settings={},
            custom_instruction="保留 Minecraft 术语，不要扩写",
        )

        self.assertIn("用户额外要求：保留 Minecraft 术语，不要扩写", prompt)
        self.assertIn("同一个 id 可以返回多条", prompt)
        self.assertNotIn("必须保持条目数量和 id 不变", prompt)

    def test_system_prompt_length_rule_is_not_overridden_by_count_lock(self):
        """一键完成的提示词要求控制长度时，后端不能再强制一条进一条出"""
        engine = TextEngine()

        prompt = engine._build_subtitle_entries_prompt(
            [{"index": 1, "start": "00:00:19,100", "end": "00:00:22,380", "text": "I joined in with a plan to make the best possible base all while keeping it completely hidden from the rest of the server"}],
            operation="translate",
            target_language="zh-CN",
            settings={"system_prompt": "每条建议 8 到 18 个汉字，最多不要超过 22 个汉字，不要把词组拆开。"},
        )

        self.assertIn("每条建议 8 到 18 个汉字", prompt)
        self.assertIn("优先遵守上面的长度、语义断句和词组保护要求", prompt)
        self.assertIn("\"id\":1", prompt)
        self.assertIn("同一个 id 可以返回多条", prompt)

    def test_translate_can_split_one_source_entry_into_multiple_timed_entries(self):
        """模型按提示词把一条长字幕拆成多条时，后端要保留拆分而不是覆盖成一条"""
        engine = FakeTextEngine([
            '[{"id":1,"text":"我加入并制定了一个计划"},{"id":1,"text":"要建造一个最好的基地"}]',
        ])
        entries = [
            {
                "index": 11,
                "start": "00:00:19,100",
                "end": "00:00:22,380",
                "text": "I joined in with a plan to make the best possible base all while keeping it completely hidden from the rest of the server",
            },
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={"subtitle_batch_size": 10, "retry_count": 0},
            operation="translate",
            target_language="zh-CN",
        ))

        self.assertEqual([entry["text"] for entry in result], ["我加入并制定了一个计划", "要建造一个最好的基地"])
        self.assertEqual(result[0]["start"], "00:00:19,100")
        self.assertEqual(result[-1]["end"], "00:00:22,380")
        self.assertEqual(result[0]["source_index"], 11)
        self.assertEqual(result[1]["source_index"], 11)
        self.assertLess(result[0]["end"], result[1]["end"])

    def test_translate_does_not_split_too_short_timeline_into_flash_entries(self):
        """原时间段过短时，即使模型返回多条译文，也要合并避免烧录成闪字幕"""
        engine = FakeTextEngine([
            '[{"id":1,"text":"如果我在地面上建一部分基地"},{"id":1,"text":"技术上就没有违规"}]',
        ])
        entries = [
            {
                "index": 46,
                "start": "00:01:44,024",
                "end": "00:01:44,224",
                "text": "If I build a section of my base above ground technically I'm not breaking any rules",
            },
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={"subtitle_batch_size": 10, "retry_count": 0},
            operation="translate",
            target_language="zh-CN",
        ))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], "00:01:44,024")
        self.assertEqual(result[0]["end"], "00:01:44,224")
        self.assertEqual(result[0]["text"], "如果我在地面上建一部分基地技术上就没有违规")

    def test_translate_requires_all_entries_to_avoid_untranslated_leftovers(self):
        """翻译批次返回不完整时抛错，避免部分原文混入结果"""
        engine = FakeTextEngine([
            '[{"id":1,"text":"第一条"}]',
        ])
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "one"},
            {"index": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "two"},
        ]

        with self.assertRaisesRegex(RuntimeError, "条数不完整"):
            asyncio.run(engine.process_subtitle_entries(
                entries=entries,
                provider_type="openai",
                api_key="test",
                base_url="https://example.com/v1",
                model="model",
                settings={"subtitle_batch_size": 2, "retry_count": 0},
                operation="translate",
                target_language="zh-CN",
            ))

    def test_process_subtitle_entries_parses_numbered_lines(self):
        """编号行返回也能合并回原字幕条目"""
        engine = FakeTextEngine([
            "1. 第一条\n2. 第二条",
        ])
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "one"},
            {"index": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "two"},
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={"subtitle_batch_size": 2},
            operation="polish",
        ))

        self.assertEqual([entry["text"] for entry in result], ["第一条", "第二条"])

    def test_process_subtitle_entries_batches_and_retries(self):
        """批量字幕处理会分批，并按配置重试失败请求"""
        engine = FakeTextEngine([
            RuntimeError("temporary"),
            '[{"id":1,"text":"第一批"}]',
            '[{"id":1,"text":"第二批"}]',
        ])
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "one"},
            {"index": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "two"},
        ]

        result = asyncio.run(engine.process_subtitle_entries(
            entries=entries,
            provider_type="openai",
            api_key="test",
            base_url="https://example.com/v1",
            model="model",
            settings={
                "subtitle_batch_size": 1,
                "concurrency": 1,
                "retry_count": 1,
                "retry_interval_ms": 1,
            },
            operation="polish",
        ))

        self.assertEqual(engine.calls, 3)
        self.assertEqual([entry["text"] for entry in result], ["第一批", "第二批"])

    def test_translate_fallback_avoids_cjk_word_boundary_breaks(self):
        """模型返回非 JSON 整句译文时，也不能把“产生”切成“产 / 生”"""
        original_entries = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "aaaaaaaaaaaaaa"},
            {"index": 2, "start": "00:00:03,000", "end": "00:00:04,000", "text": "bbb"},
        ]

        merged = TextEngine()._merge_processed_entries(
            original_entries,
            "为了让你再也无法对别的女人产生反应",
            require_all=True,
        )

        self.assertEqual(len(merged), 2)
        self.assertEqual("".join(str(entry["text"]) for entry in merged), "为了让你再也无法对别的女人产生反应")
        self.assertFalse(str(merged[0]["text"]).endswith("产"))
        self.assertTrue(str(merged[1]["text"]).startswith("产生"))


if __name__ == "__main__":
    unittest.main()
