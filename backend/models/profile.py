# backend/models/profile.py
# API 配置数据模型 - 存储文本 API 和配音 API 的配置

from sqlalchemy import Column, Integer, String, DateTime, Text, func
from .database import Base


class TextProviderProfile(Base):
    """
    文本 API 配置模型
    存储 OpenAI、Gemini、Anthropic 等文本 API 的配置
    """
    __tablename__ = "text_provider_profiles"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 配置名称
    name = Column(String(100), nullable=False)
    # 渠道类型（openai、openai_compatible、gemini、gemini_compatible、anthropic、custom）
    provider_type = Column(String(50), nullable=False)
    # API 基础地址
    base_url = Column(String(500), nullable=False)
    # API 密钥（AES 加密存储）
    api_key_encrypted = Column(Text, nullable=False)
    # 模型名称
    model = Column(String(100), nullable=True)
    # 额外参数（JSON 字符串）
    extra_params = Column(Text, nullable=True)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class VoiceProviderProfile(Base):
    """
    配音 API 配置模型
    存储 OpenAI TTS、Gemini TTS、MiniMax、小米 MiMo 等配音 API 的配置
    """
    __tablename__ = "voice_provider_profiles"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 配置名称
    name = Column(String(100), nullable=False)
    # 渠道类型（openai_tts、gemini_tts、minimax_tts、xiaomi_mimo_tts、gpt_sovits、index_tts2、local_tts、custom_tts）
    provider_type = Column(String(50), nullable=False)
    # API 基础地址；local_tts 可为空或填写命令模板，index_tts2 填本地项目目录
    base_url = Column(String(500), nullable=False)
    # API 密钥（AES 加密存储）
    api_key_encrypted = Column(Text, nullable=False)
    # 模型/语音名称
    voice = Column(String(100), nullable=True)
    # 额外参数（JSON 字符串）
    extra_params = Column(Text, nullable=True)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
