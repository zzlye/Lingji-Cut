# backend/tests/test_voice_profiles.py
# 配音配置测试 - 验证配音模型获取和真实连接测试入口

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.profiles import _fetch_voice_models, _test_voice_profile, TextModelOption, VoiceProfileTestRequest, _transient_voice_profile  # noqa: E402
from backend.api.voice import VoiceCatalogRequest, VoicePreviewRequest, _audio_format_for_preview, get_voice_catalog, preview_voice  # noqa: E402
from backend.core.voice_engine import VoiceEngine  # noqa: E402
from backend.models import VoiceProviderProfile  # noqa: E402


class VoiceProfileTests(unittest.TestCase):
    """配音配置接口单元测试"""

    def test_fetch_voice_models_filters_remote_tts_models(self):
        """配音模型获取会优先使用远程模型并过滤 TTS 相关项"""
        async def run():
            with patch("backend.api.profiles._fetch_text_models", new=AsyncMock(return_value=[
                TextModelOption(id="gpt-4.1-mini", label="gpt-4.1-mini"),
                TextModelOption(id="gpt-4o-mini-tts", label="gpt-4o-mini-tts"),
                TextModelOption(id="tts-1-hd", label="tts-1-hd"),
            ])):
                models, source = await _fetch_voice_models("openai_tts", "https://api.example.com/v1", "key")
                return models, source

        models, source = asyncio.run(run())

        self.assertEqual(source, "remote")
        self.assertEqual([model.id for model in models], ["gpt-4o-mini-tts", "tts-1-hd"])

    def test_fetch_voice_models_falls_back_to_builtin_defaults(self):
        """远程模型失败时返回内置配音模型，避免 UI 空白"""
        async def run():
            with patch("backend.api.profiles._fetch_text_models", new=AsyncMock(side_effect=RuntimeError("network"))):
                return await _fetch_voice_models("minimax_tts", "https://api.example.com/v1", "key")

        models, source = asyncio.run(run())

        self.assertEqual(source, "local")
        self.assertTrue(any(model.id == "speech-2.8-hd" for model in models))

    def test_test_voice_profile_generates_short_audio(self):
        """测试连接会调用配音引擎生成短试听，而不是空返回成功"""
        profile = VoiceProviderProfile(
            id=1,
            name="测试配音",
            provider_type="openai_tts",
            base_url="https://api.example.com/v1",
            voice="gpt-4o-mini-tts",
            extra_params='{"voice":"alloy","format":"mp3"}',
        )

        async def run():
            with patch("backend.api.profiles.VoiceEngine") as engine_class:
                engine = engine_class.return_value
                engine.generate_voice = AsyncMock(return_value=os.path.join(os.environ.get("TEMP", "."), "missing.mp3"))
                await _test_voice_profile(profile, "key")
                return engine.generate_voice.call_args.kwargs

        kwargs = asyncio.run(run())

        self.assertEqual(kwargs["text"], "配音连接测试。")
        self.assertEqual(kwargs["provider_type"], "openai_tts")
        self.assertEqual(kwargs["voice"], "alloy")
        self.assertEqual(kwargs["model"], "gpt-4o-mini-tts")

    def test_transient_voice_profile_uses_unsaved_form_fields(self):
        """未保存表单测试时使用当前填写的渠道、地址和模型"""
        request = VoiceProfileTestRequest(
            name="临时测试",
            provider_type="minimax_tts",
            base_url="https://api.example.com/v1",
            model="speech-2.8-hd",
            extra_params='{"voice":"Chinese_Professional_Male"}',
        )

        profile = _transient_voice_profile(request, None)

        self.assertEqual(profile.name, "临时测试")
        self.assertEqual(profile.provider_type, "minimax_tts")
        self.assertEqual(profile.base_url, "https://api.example.com/v1")
        self.assertEqual(profile.voice, "speech-2.8-hd")

    def test_preview_voice_uses_unsaved_form_fields(self):
        """试听接口支持未保存配置，直接使用当前表单参数生成音频"""
        request = VoicePreviewRequest(
            text="试听文本",
            provider_type="openai_tts",
            base_url="https://api.example.com/v1",
            api_key="key",
            voice="nova",
            model="gpt-4o-mini-tts",
            settings={"format": "mp3", "speed": 1.1},
        )

        async def run():
            with patch("backend.api.voice.VoiceEngine") as engine_class:
                engine = engine_class.return_value
                engine.generate_voice = AsyncMock(return_value=os.path.join(os.environ.get("TEMP", "."), "preview.mp3"))
                result = await preview_voice(request, db=None)
                return result, engine.generate_voice.call_args.kwargs

        result, kwargs = asyncio.run(run())

        self.assertEqual(result["message"], "试听音频已生成")
        self.assertIn("/voice/audio?path=", result["audio_url"])
        self.assertEqual(kwargs["text"], "试听文本")
        self.assertEqual(kwargs["provider_type"], "openai_tts")
        self.assertEqual(kwargs["voice"], "nova")
        self.assertEqual(kwargs["model"], "gpt-4o-mini-tts")
        self.assertEqual(kwargs["settings"]["speed"], 1.1)

    def test_gemini_preview_uses_wav_output(self):
        """Gemini TTS 返回 PCM，试听文件需要封装为浏览器可播放的 WAV"""
        self.assertEqual(_audio_format_for_preview("gemini_tts", {"format": "mp3"}), "wav")
        self.assertEqual(VoiceEngine()._provider_audio_format("gemini_tts", {"format": "mp3"}), "wav")
        self.assertEqual(_audio_format_for_preview("custom_tts", {"format": "mp3"}, "mimo-v2.5-tts"), "wav")

    def test_xiaomi_mimo_headers_support_newapi_bearer_token(self):
        """小米 MiMo 经过 NewAPI 时必须发送 Bearer Token，否则会被判定为 Invalid token"""
        headers = VoiceEngine()._xiaomi_mimo_headers("sk-test")

        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["api-key"], "sk-test")
        self.assertEqual(headers["x-api-key"], "sk-test")

    def test_mimo_model_uses_mimo_protocol_even_in_custom_channel(self):
        """NewAPI 里的 MiMo 模型即使放在自定义渠道，也不能走 OpenAI audio/speech"""
        self.assertEqual(VoiceEngine.resolve_provider_type("custom_tts", "mimo-v2.5-tts"), "xiaomi_mimo_tts")
        self.assertEqual(VoiceEngine.resolve_provider_type("openai_tts", "mimo-v2-tts"), "xiaomi_mimo_tts")
        self.assertEqual(VoiceEngine.resolve_provider_type("openai_tts", "gpt-4o-mini-tts"), "openai_tts")

    def test_gemini_tts_model_uses_gemini_voice_catalog_in_custom_channel(self):
        """NewAPI 自定义渠道里选择 Gemini TTS 模型时应返回 Gemini 内置音色"""
        self.assertEqual(VoiceEngine.resolve_provider_type("custom_tts", "gemini-3.1-flash-tts-preview"), "gemini_tts")

        result = asyncio.run(get_voice_catalog(VoiceCatalogRequest(
            provider_type="custom_tts",
            model="gemini-3.1-flash-tts-preview",
        )))

        voice_ids = [voice["id"] for voice in result["voices"]]
        self.assertIn("Kore", voice_ids)
        self.assertIn("Puck", voice_ids)
        self.assertNotIn("custom", voice_ids)


if __name__ == "__main__":
    unittest.main()
