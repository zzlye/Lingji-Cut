# backend/tests/test_voice_profiles.py
# 配音配置测试 - 验证配音模型获取和真实连接测试入口

import asyncio
import base64
import os
import sys
import tempfile
import unittest
import wave
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

    def _capture_xiaomi_payload(self, model: str, voice: str, settings: dict) -> dict:
        """捕获小米 MiMo TTS 请求体，避免单测真实访问外部接口"""
        captured_payloads: list[dict] = []

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                """模拟小米返回 base64 音频"""
                return {"choices": [{"message": {"audio": {"data": base64.b64encode(b"audio").decode("ascii")}}}]}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *_args, **kwargs):
                captured_payloads.append(kwargs["json"])
                return FakeResponse()

        with tempfile.TemporaryDirectory(prefix="xiaomi_payload_") as temp_dir:
            output_path = os.path.join(temp_dir, "voice.wav")
            with patch("httpx.AsyncClient", FakeClient):
                asyncio.run(VoiceEngine()._generate_xiaomi_mimo_tts(
                    text="小米配音测试",
                    output_path=output_path,
                    voice=voice,
                    api_key="key",
                    base_url="https://api.xiaomimimo.com/v1",
                    model=model,
                    settings=settings,
                ))

        self.assertEqual(len(captured_payloads), 1)
        return captured_payloads[0]

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
        self.assertEqual(_audio_format_for_preview("custom_tts", {"format": "mp3"}, "mimo-v2.5-tts"), "mp3")
        self.assertEqual(_audio_format_for_preview("custom_tts", {"format": "mp3"}, "gemini-3.1-flash-tts-preview"), "mp3")
        self.assertEqual(
            VoiceEngine()._provider_audio_format("custom_tts", {"format": "mp3"}, "gemini-3.1-flash-tts-preview"),
            "mp3",
        )

    def test_xiaomi_mimo_voice_catalog_follows_selected_tts_model(self):
        """小米 MiMo 不同 TTS 模型展示不同音色入口，避免把预置、设计和克隆混在一起"""
        built_in = asyncio.run(get_voice_catalog(VoiceCatalogRequest(provider_type="xiaomi_mimo_tts", model="mimo-v2.5-tts")))
        voice_design = asyncio.run(get_voice_catalog(VoiceCatalogRequest(provider_type="xiaomi_mimo_tts", model="mimo-v2.5-tts-voicedesign")))
        voice_clone = asyncio.run(get_voice_catalog(VoiceCatalogRequest(provider_type="xiaomi_mimo_tts", model="mimo-v2.5-tts-voiceclone")))

        self.assertIn("白桦", [voice["id"] for voice in built_in["voices"]])
        self.assertEqual(next(voice for voice in built_in["voices"] if voice["id"] == "苏打")["gender"], "male")
        self.assertTrue(any(str(voice["id"]).startswith("voice_design:") for voice in voice_design["voices"]))
        self.assertEqual([voice["id"] for voice in voice_clone["voices"]], ["voice_clone"])

    def test_xiaomi_mimo_v25_builtin_voice_uses_audio_voice(self):
        """mimo-v2.5-tts 预置音色按官方格式写入 audio.voice"""
        payload = self._capture_xiaomi_payload(
            model="mimo-v2.5-tts",
            voice="白桦",
            settings={"format": "wav", "style_prompt": "自然朗读。"},
        )

        self.assertEqual(payload["model"], "mimo-v2.5-tts")
        self.assertEqual(payload["audio"]["voice"], "白桦")
        self.assertEqual(payload["audio"]["format"], "wav")
        self.assertNotIn("modalities", payload)
        self.assertEqual(payload["messages"][1]["content"], "小米配音测试")

    def test_xiaomi_mimo_voice_design_uses_prompt_without_audio_voice(self):
        """VoiceDesign 使用文字音色描述，不再把描述塞进 audio.voice"""
        payload = self._capture_xiaomi_payload(
            model="mimo-v2.5-tts-voicedesign",
            voice="voice_design:年轻男声，普通话标准。",
            settings={"format": "mp3", "style_prompt": "不要夸张换气。"},
        )

        self.assertEqual(payload["model"], "mimo-v2.5-tts-voicedesign")
        self.assertEqual(payload["audio"]["format"], "mp3")
        self.assertNotIn("voice", payload["audio"])
        self.assertIn("年轻男声", payload["messages"][0]["content"])
        self.assertIn("不要夸张换气", payload["messages"][0]["content"])

    def test_xiaomi_mimo_voice_design_voice_overrides_global_prompt(self):
        """多人配音时 voice_design: 音色描述应覆盖全局描述，避免所有角色同一个音色"""
        payload = self._capture_xiaomi_payload(
            model="mimo-v2.5-tts-voicedesign",
            voice="voice_design:沉稳男声，适合旁白。",
            settings={"format": "wav", "xiaomi_voice_design_prompt": "年轻女声。"},
        )

        self.assertIn("沉稳男声", payload["messages"][0]["content"])
        self.assertNotIn("年轻女声", payload["messages"][0]["content"])

    def test_xiaomi_mimo_voice_clone_uses_audio_sample_data_uri(self):
        """VoiceClone 按官方要求把 mp3/wav 样本转为 data URI 放入 audio.voice"""
        with tempfile.TemporaryDirectory(prefix="xiaomi_clone_") as temp_dir:
            sample_path = os.path.join(temp_dir, "sample.wav")
            with open(sample_path, "wb") as file:
                file.write(b"sample-audio")
            payload = self._capture_xiaomi_payload(
                model="mimo-v2.5-tts-voiceclone",
                voice="voice_clone",
                settings={"format": "wav", "xiaomi_voice_clone_audio_path": sample_path},
            )

        self.assertEqual(payload["model"], "mimo-v2.5-tts-voiceclone")
        self.assertTrue(payload["audio"]["voice"].startswith("data:audio/wav;base64,"))
        self.assertEqual(base64.b64decode(payload["audio"]["voice"].split(",", 1)[1]), b"sample-audio")

    def test_xiaomi_mimo_voice_clone_voice_path_overrides_global_sample(self):
        """多人配音时 voice_clone_path: 应使用当前角色样本，而不是全局样本"""
        with tempfile.TemporaryDirectory(prefix="xiaomi_clone_override_") as temp_dir:
            global_path = os.path.join(temp_dir, "global.wav")
            role_path = os.path.join(temp_dir, "role.mp3")
            with open(global_path, "wb") as file:
                file.write(b"global-audio")
            with open(role_path, "wb") as file:
                file.write(b"role-audio")
            payload = self._capture_xiaomi_payload(
                model="mimo-v2.5-tts-voiceclone",
                voice=f"voice_clone_path:{role_path}",
                settings={"format": "wav", "xiaomi_voice_clone_audio_path": global_path},
            )

        self.assertTrue(payload["audio"]["voice"].startswith("data:audio/mpeg;base64,"))
        self.assertEqual(base64.b64decode(payload["audio"]["voice"].split(",", 1)[1]), b"role-audio")

    def test_openai_compatible_gemini_model_keeps_requested_format(self):
        """OpenAI 兼容渠道由渠道决定协议，不能因为模型名包含 Gemini 就强行改格式"""
        engine = VoiceEngine()
        captured_payloads = []

        class FakeResponse:
            status_code = 200
            content = b"RIFF" + (b"\0" * 2048)
            headers = {"content-type": "audio/wav"}

            @property
            def text(self):
                return ""

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *_args, **kwargs):
                captured_payloads.append(kwargs["json"])
                return FakeResponse()

        with tempfile.TemporaryDirectory(prefix="voice_payload_") as temp_dir:
            output_path = os.path.join(temp_dir, "voice.wav")
            with patch("httpx.AsyncClient", FakeClient):
                asyncio.run(engine._generate_openai_tts(
                    text="格式测试",
                    output_path=output_path,
                    voice="Kore",
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    model="gemini-3.1-flash-tts-preview",
                    settings={"format": "mp3"},
                    provider_type="custom_tts",
                ))

        self.assertEqual(captured_payloads[0]["response_format"], "mp3")

    def test_custom_openai_compatible_gemini_model_uses_openai_route(self):
        """自定义 OpenAI 兼容渠道必须走 audio/speech，模型名不能改成 Gemini 协议"""
        engine = VoiceEngine()
        calls = {"openai": 0, "gemini": 0}

        async def fake_openai(*_args, **_kwargs):
            """记录 OpenAI 兼容调用"""
            calls["openai"] += 1
            return "ok.mp3"

        async def fake_gemini(*_args, **_kwargs):
            """如果走到 Gemini 分支，说明协议选择被模型名带偏"""
            calls["gemini"] += 1
            return "bad.wav"

        with tempfile.TemporaryDirectory(prefix="voice_route_") as temp_dir:
            output_path = os.path.join(temp_dir, "ignored.mp3")
            with (
                patch.object(engine, "_generate_openai_tts", side_effect=fake_openai),
                patch.object(engine, "_generate_gemini_tts", side_effect=fake_gemini),
                patch.object(engine, "_validate_voice_output"),
            ):
                result = asyncio.run(engine.generate_voice(
                    text="OpenAI 兼容路由测试",
                    output_path=output_path,
                    provider_type="custom_tts",
                    voice="Kore",
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    model="gemini-3.1-flash-tts-preview",
                    settings={"format": "mp3", "retry_count": 0},
                ))

        self.assertEqual(result, "ok.mp3")
        self.assertEqual(calls, {"openai": 1, "gemini": 0})

    def test_custom_openai_compatible_requires_explicit_model(self):
        """自定义 OpenAI 兼容不能静默回退默认模型，避免 NewAPI 分组模型不匹配"""
        engine = VoiceEngine()

        with tempfile.TemporaryDirectory(prefix="voice_custom_model_") as temp_dir:
            output_path = os.path.join(temp_dir, "voice.mp3")
            with self.assertRaises(ValueError) as context:
                asyncio.run(engine._generate_openai_tts(
                    text="缺少模型测试",
                    output_path=output_path,
                    voice="Kore",
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    model="",
                    settings={"format": "mp3"},
                    provider_type="custom_tts",
                ))

        self.assertIn("必须填写模型", str(context.exception))

    def test_newapi_error_message_is_human_readable(self):
        """NewAPI 返回 JSON 错误时，只展示真正的错误消息"""
        class FakeResponse:
            status_code = 503
            text = '{"error":{"code":"model_not_found","message":"模型无可用渠道","type":"new_api_error"}}'

            def json(self):
                """模拟 NewAPI 错误响应"""
                return {"error": {"code": "model_not_found", "message": "模型无可用渠道"}}

        message = VoiceEngine()._response_error_message(FakeResponse())

        self.assertEqual(message, "模型无可用渠道（model_not_found）")

    def test_empty_exception_message_uses_exception_type(self):
        """底层异常没有文本时也要给前端可读内容"""
        self.assertEqual(VoiceEngine()._exception_message(TimeoutError()), "TimeoutError")

    def test_xiaomi_mimo_headers_support_newapi_bearer_token(self):
        """小米 MiMo 经过 NewAPI 时必须发送 Bearer Token，否则会被判定为 Invalid token"""
        headers = VoiceEngine()._xiaomi_mimo_headers("sk-test")

        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["api-key"], "sk-test")
        self.assertEqual(headers["x-api-key"], "sk-test")

    def test_model_name_does_not_override_selected_voice_provider(self):
        """用户选择 OpenAI 兼容渠道时，不能因为模型名像其他渠道就隐式改协议"""
        self.assertEqual(VoiceEngine.resolve_provider_type("custom_tts", "mimo-v2.5-tts"), "custom_tts")
        self.assertEqual(VoiceEngine.resolve_provider_type("openai_tts", "mimo-v2-tts"), "openai_tts")
        self.assertEqual(VoiceEngine.resolve_provider_type("openai_tts", "gpt-4o-mini-tts"), "openai_tts")

    def test_gemini_tts_model_uses_gemini_voice_catalog_in_custom_channel(self):
        """自定义 OpenAI 兼容渠道不能因为模型名像 Gemini 就强行切换音色目录"""
        self.assertEqual(VoiceEngine.resolve_provider_type("custom_tts", "gemini-3.1-flash-tts-preview"), "custom_tts")

        result = asyncio.run(get_voice_catalog(VoiceCatalogRequest(
            provider_type="custom_tts",
            model="gemini-3.1-flash-tts-preview",
        )))

        voice_ids = [voice["id"] for voice in result["voices"]]
        self.assertIn("custom", voice_ids)
        self.assertNotIn("Kore", voice_ids)

    def test_generate_voice_retries_before_success(self):
        """配音生成遇到临时失败会按配置自动重试"""
        with tempfile.TemporaryDirectory(prefix="voice_retry_") as temp_dir:
            output_path = os.path.join(temp_dir, "retry.mp3")
            engine = VoiceEngine()
            calls = {"count": 0}

            async def fake_once(*_args, **_kwargs):
                calls["count"] += 1
                if calls["count"] < 3:
                    raise RuntimeError("temporary unavailable")
                with open(output_path, "wb") as file:
                    file.write(b"voice" * 512)
                return output_path

            with (
                patch.object(engine, "_generate_voice_once", side_effect=fake_once),
                patch.object(engine, "_audio_duration_seconds", return_value=1.0),
                patch("backend.core.voice_engine.asyncio.sleep", new=AsyncMock()),
            ):
                result = asyncio.run(engine.generate_voice(
                    text="配音重试测试",
                    output_path=output_path,
                    provider_type="openai_tts",
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    model="gpt-4o-mini-tts",
                    settings={"retry_count": 2, "retry_interval_ms": 1},
                ))

        self.assertEqual(result, output_path)
        self.assertEqual(calls["count"], 3)

    def test_generate_voice_retries_when_audio_output_is_too_short(self):
        """配音接口返回极短静音文件时不能标记成功，应删除半成品并重试"""
        with tempfile.TemporaryDirectory(prefix="voice_short_retry_") as temp_dir:
            output_path = os.path.join(temp_dir, "short.wav")
            engine = VoiceEngine()
            calls = {"count": 0}

            async def fake_once(*_args, **_kwargs):
                calls["count"] += 1
                with open(output_path, "wb") as file:
                    file.write(b"0" * 2048)
                return output_path

            with (
                patch.object(engine, "_generate_voice_once", side_effect=fake_once),
                patch.object(engine, "_audio_duration_seconds", side_effect=[0.04, 1.25]),
                patch("backend.core.voice_engine.asyncio.sleep", new=AsyncMock()),
            ):
                result = asyncio.run(engine.generate_voice(
                    text="极短音频重试测试",
                    output_path=output_path,
                    provider_type="openai_tts",
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    model="gpt-4o-mini-tts",
                    settings={"retry_count": 1, "retry_interval_ms": 1},
                ))

        self.assertEqual(result, output_path)
        self.assertEqual(calls["count"], 2)

    def test_generate_voice_rejects_unreadable_audio_duration(self):
        """接口返回不可解码音频时不能显示成功，否则前端播放器会变成 0:00"""
        with tempfile.TemporaryDirectory(prefix="voice_bad_audio_") as temp_dir:
            output_path = os.path.join(temp_dir, "bad.mp3")
            engine = VoiceEngine()

            async def fake_once(*_args, **_kwargs):
                """写入大于 1KB 但不可播放的假音频"""
                with open(output_path, "wb") as file:
                    file.write(b"not-a-real-audio" * 200)
                return output_path

            with (
                patch.object(engine, "_generate_voice_once", side_effect=fake_once),
                patch.object(engine, "_audio_duration_seconds", return_value=None),
            ):
                with self.assertRaises(RuntimeError) as context:
                    asyncio.run(engine.generate_voice(
                        text="不可解码音频测试",
                        output_path=output_path,
                        provider_type="openai_tts",
                        api_key="key",
                        base_url="https://api.example.com/v1",
                        model="gpt-4o-mini-tts",
                        settings={"retry_count": 0},
                    ))

        self.assertIn("无法读取时长", str(context.exception))

    def test_generate_voice_rejects_silent_wav_output(self):
        """接口返回只有 WAV 头和静音数据时不能显示成功，否则试听播放器会是 0:00"""
        with tempfile.TemporaryDirectory(prefix="voice_silent_audio_") as temp_dir:
            output_path = os.path.join(temp_dir, "silent.wav")
            engine = VoiceEngine()

            async def fake_once(*_args, **_kwargs):
                """写入足够大但没有声音的 WAV"""
                with wave.open(output_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(24000)
                    wav_file.writeframes(b"\0" * 24000)
                return output_path

            with patch.object(engine, "_generate_voice_once", side_effect=fake_once):
                with self.assertRaises(RuntimeError) as context:
                    asyncio.run(engine.generate_voice(
                        text="静音音频测试",
                        output_path=output_path,
                        provider_type="custom_tts",
                        api_key="key",
                        base_url="https://api.example.com/v1",
                        model="gemini-3.1-flash-tts-preview",
                        settings={"retry_count": 0, "format": "wav"},
                    ))

        self.assertIn("接近静音", str(context.exception))


if __name__ == "__main__":
    unittest.main()
