# API Key 按需读取测试 - 验证文本/配音配置可单独取回已保存密钥

import asyncio
import os
import sys
import unittest

from fastapi import HTTPException

# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.profiles import ProfileCreate, ProfileRename, ProfileUpdate, create_text_profile, create_voice_profile, delete_text_profile, get_text_profile_secret, get_voice_profile_secret, rename_text_profile, update_text_profile, update_voice_profile  # noqa: E402
from backend.models import TextProviderProfile, VoiceProviderProfile  # noqa: E402
from backend.utils import encrypt_api_key  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟 SQLAlchemy 最小能力"""

    def __init__(self, items):
        self.items = items

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.items[0] if self.items else None


class FakeDb:
    """测试用数据库会话，按模型返回预置对象"""

    def __init__(self, text_profiles=None, voice_profiles=None):
        self.text_profiles = text_profiles or []
        self.voice_profiles = voice_profiles or []

    def query(self, model):
        if model is TextProviderProfile:
            return FakeQuery(self.text_profiles)
        if model is VoiceProviderProfile:
            return FakeQuery(self.voice_profiles)
        return FakeQuery([])

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = len(self.text_profiles) + len(self.voice_profiles) + 1
        if isinstance(item, TextProviderProfile):
            self.text_profiles.append(item)
        elif isinstance(item, VoiceProviderProfile):
            self.voice_profiles.append(item)

    def commit(self):
        return None

    def refresh(self, _item):
        return None

    def delete(self, item):
        if isinstance(item, TextProviderProfile):
            self.text_profiles = [profile for profile in self.text_profiles if profile is not item]
        elif isinstance(item, VoiceProviderProfile):
            self.voice_profiles = [profile for profile in self.voice_profiles if profile is not item]


class ProfileSecretTests(unittest.TestCase):
    """配置密钥读取测试"""

    def test_get_text_profile_secret_returns_decrypted_key(self):
        """文本配置密钥接口返回解密后的 API Key"""
        db = FakeDb(text_profiles=[
            TextProviderProfile(id=1, name="文本", provider_type="openai", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-text"), model="gpt"),
        ])

        result = asyncio.run(get_text_profile_secret(1, db))

        self.assertEqual(result.api_key, "sk-text")

    def test_get_voice_profile_secret_returns_decrypted_key(self):
        """配音配置密钥接口返回解密后的 API Key"""
        db = FakeDb(voice_profiles=[
            VoiceProviderProfile(id=2, name="配音", provider_type="openai_tts", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-voice"), voice="tts"),
        ])

        result = asyncio.run(get_voice_profile_secret(2, db))

        self.assertEqual(result.api_key, "sk-voice")

    def test_create_text_profile_rejects_empty_api_key(self):
        """新建文本 API 配置时不允许保存空密钥"""
        with self.assertRaises(HTTPException) as context:
            asyncio.run(create_text_profile(ProfileCreate(name="文本", provider_type="openai", base_url="https://example.com", api_key="", model="gpt"), FakeDb()))

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("API Key", context.exception.detail)

    def test_create_local_tts_voice_profile_allows_empty_api_key(self):
        """本地 TTS 配置不需要 API Key，也允许命令模板放在高级参数里"""
        db = FakeDb()

        result = asyncio.run(create_voice_profile(ProfileCreate(
            name="本地配音",
            provider_type="local_tts",
            base_url="",
            api_key="",
            model="local-command",
            extra_params='{"local_tts_command":"python infer.py --text-file {text_file} --output {output}"}',
        ), db))

        self.assertEqual(result.provider_type, "local_tts")
        self.assertEqual(result.base_url, "")
        self.assertEqual(len(db.voice_profiles), 1)

    def test_create_index_tts2_voice_profile_allows_empty_api_key(self):
        """IndexTTS2 本地配音配置不需要 API Key"""
        db = FakeDb()

        result = asyncio.run(create_voice_profile(ProfileCreate(
            name="IndexTTS2",
            provider_type="index_tts2",
            base_url="D:/tools/index-tts",
            api_key="",
            model="index-tts2",
            extra_params='{"index_tts2_speaker_audio_path":"D:/tmp/ref.wav"}',
        ), db))

        self.assertEqual(result.provider_type, "index_tts2")
        self.assertEqual(result.base_url, "D:/tools/index-tts")
        self.assertEqual(len(db.voice_profiles), 1)

    def test_remote_voice_profile_still_rejects_empty_api_key(self):
        """远程配音配置仍然不允许新建空密钥配置"""
        with self.assertRaises(HTTPException) as context:
            asyncio.run(create_voice_profile(ProfileCreate(name="远程配音", provider_type="openai_tts", base_url="https://example.com/v1", api_key="", model="tts"), FakeDb()))

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("API Key", context.exception.detail)

    def test_update_local_tts_voice_profile_can_clear_api_key(self):
        """远程配置改成本地 TTS 时可以清空旧密钥，避免多余密钥留在配置里"""
        profile = VoiceProviderProfile(id=2, name="旧配音", provider_type="openai_tts", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-voice"), voice="tts")
        db = FakeDb(voice_profiles=[profile])

        result = asyncio.run(update_voice_profile(2, ProfileUpdate(
            name="本地配音",
            provider_type="local_tts",
            base_url="",
            api_key=None,
            model="local-command",
            extra_params='{"local_tts_command":"python infer.py --text-file {text_file} --output {output}"}',
        ), db))

        self.assertEqual(result.provider_type, "local_tts")
        self.assertEqual(result.base_url, "")

    def test_update_index_tts2_voice_profile_can_clear_api_key(self):
        """远程配置改成 IndexTTS2 时可以清空旧密钥"""
        profile = VoiceProviderProfile(id=3, name="旧配音", provider_type="openai_tts", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-voice"), voice="tts")
        db = FakeDb(voice_profiles=[profile])

        result = asyncio.run(update_voice_profile(3, ProfileUpdate(
            name="IndexTTS2",
            provider_type="index_tts2",
            base_url="D:/tools/index-tts",
            api_key=None,
            model="index-tts2",
            extra_params='{"index_tts2_speaker_audio_path":"D:/tmp/ref.wav"}',
        ), db))

        self.assertEqual(result.provider_type, "index_tts2")
        self.assertEqual(result.base_url, "D:/tools/index-tts")

    def test_update_text_profile_requires_key_when_old_secret_is_empty(self):
        """旧配置没有密钥时，更新不能继续留空"""
        db = FakeDb(text_profiles=[
            TextProviderProfile(id=1, name="文本", provider_type="openai", base_url="https://example.com", api_key_encrypted="", model="gpt"),
        ])

        with self.assertRaises(HTTPException) as context:
            asyncio.run(update_text_profile(1, ProfileUpdate(name="文本", provider_type="openai", base_url="https://example.com", api_key=None, model="gpt"), db))

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("API Key", context.exception.detail)

    def test_rename_text_profile_only_changes_name(self):
        """文本配置改名接口不改动渠道和密钥"""
        profile = TextProviderProfile(id=1, name="旧名称", provider_type="openai", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-text"), model="gpt")
        db = FakeDb(text_profiles=[profile])

        result = asyncio.run(rename_text_profile(1, ProfileRename(name="新名称"), db))

        self.assertEqual(result.name, "新名称")
        self.assertEqual(profile.provider_type, "openai")
        self.assertEqual(profile.model, "gpt")

    def test_delete_text_profile_removes_profile(self):
        """文本配置删除接口会移除选中的配置"""
        profile = TextProviderProfile(id=1, name="文本", provider_type="openai", base_url="https://example.com", api_key_encrypted=encrypt_api_key("sk-text"), model="gpt")
        db = FakeDb(text_profiles=[profile])

        result = asyncio.run(delete_text_profile(1, db))

        self.assertEqual(result["profile_id"], 1)
        self.assertEqual(db.text_profiles, [])


if __name__ == "__main__":
    unittest.main()
