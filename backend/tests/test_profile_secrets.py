# API Key 按需读取测试 - 验证文本/配音配置可单独取回已保存密钥

import asyncio
import os
import sys
import unittest


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.profiles import get_text_profile_secret, get_voice_profile_secret  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
