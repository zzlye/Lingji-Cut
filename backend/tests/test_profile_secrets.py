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

from backend.api.profiles import ProfileCreate, ProfileUpdate, create_text_profile, get_text_profile_secret, get_voice_profile_secret, update_text_profile  # noqa: E402
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
        if isinstance(item, TextProviderProfile):
            self.text_profiles.append(item)
        elif isinstance(item, VoiceProviderProfile):
            self.voice_profiles.append(item)

    def commit(self):
        return None

    def refresh(self, _item):
        return None


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

    def test_update_text_profile_requires_key_when_old_secret_is_empty(self):
        """旧配置没有密钥时，更新不能继续留空"""
        db = FakeDb(text_profiles=[
            TextProviderProfile(id=1, name="文本", provider_type="openai", base_url="https://example.com", api_key_encrypted="", model="gpt"),
        ])

        with self.assertRaises(HTTPException) as context:
            asyncio.run(update_text_profile(1, ProfileUpdate(name="文本", provider_type="openai", base_url="https://example.com", api_key=None, model="gpt"), db))

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("API Key", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
