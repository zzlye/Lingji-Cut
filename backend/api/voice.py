# backend/api/voice.py
# 配音 API 路由 - 提供配音生成接口

import json
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Any, Optional

from ..models import get_db, VoiceProviderProfile
from ..utils import decrypt_api_key
from ..core import VoiceEngine

# 创建路由器
router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceGenerateRequest(BaseModel):
    """配音生成请求"""
    text: str
    profile_id: int
    voice: Optional[str] = None
    model: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    output_path: Optional[str] = None


class VoicePreviewRequest(BaseModel):
    """未保存配音表单试听请求"""
    text: str
    profile_id: Optional[int] = None
    provider_type: str = "openai_tts"
    base_url: str = ""
    api_key: Optional[str] = None
    voice: Optional[str] = None
    model: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)


class VoiceCatalogRequest(BaseModel):
    """获取音色目录请求"""
    provider_type: str


VOICE_CATALOGS = {
    "openai_tts": [
        {"id": "alloy", "name": "Alloy", "language": "多语言", "style": "均衡、通用"},
        {"id": "ash", "name": "Ash", "language": "多语言", "style": "稳重、叙述"},
        {"id": "ballad", "name": "Ballad", "language": "多语言", "style": "柔和、故事感"},
        {"id": "coral", "name": "Coral", "language": "多语言", "style": "清晰、亲和"},
        {"id": "echo", "name": "Echo", "language": "多语言", "style": "男声、清楚"},
        {"id": "fable", "name": "Fable", "language": "多语言", "style": "表达、叙事"},
        {"id": "nova", "name": "Nova", "language": "多语言", "style": "女声、自然"},
        {"id": "onyx", "name": "Onyx", "language": "多语言", "style": "低沉、沉稳"},
        {"id": "sage", "name": "Sage", "language": "多语言", "style": "成熟、知识感"},
        {"id": "shimmer", "name": "Shimmer", "language": "多语言", "style": "明亮、轻快"},
    ],
    "gemini_tts": [
        {"id": "Kore", "name": "Kore", "language": "多语言", "style": "清晰"},
        {"id": "Puck", "name": "Puck", "language": "多语言", "style": "活泼"},
        {"id": "Charon", "name": "Charon", "language": "多语言", "style": "沉稳"},
        {"id": "Fenrir", "name": "Fenrir", "language": "多语言", "style": "厚重"},
        {"id": "Aoede", "name": "Aoede", "language": "多语言", "style": "柔和"},
        {"id": "Leda", "name": "Leda", "language": "多语言", "style": "明亮"},
        {"id": "Orus", "name": "Orus", "language": "多语言", "style": "正式"},
        {"id": "Zephyr", "name": "Zephyr", "language": "多语言", "style": "轻快"},
    ],
    "minimax_tts": [
        {"id": "Chinese_Professional_Male", "name": "中文专业男声", "language": "中文", "style": "商业解说"},
        {"id": "Chinese_Professional_Female", "name": "中文专业女声", "language": "中文", "style": "商业解说"},
        {"id": "Chinese_Gentleman", "name": "中文绅士男声", "language": "中文", "style": "沉稳"},
        {"id": "Chinese_Graceful_Lady", "name": "中文优雅女声", "language": "中文", "style": "自然"},
        {"id": "English_expressive_narrator", "name": "English Expressive Narrator", "language": "英文", "style": "叙事"},
        {"id": "English_Graceful_Lady", "name": "English Graceful Lady", "language": "英文", "style": "优雅"},
    ],
    "xiaomi_mimo_tts": [
        {"id": "mimo_default", "name": "MiMo 默认", "language": "中文/英文", "style": "默认"},
        {"id": "default_zh", "name": "MiMo 中文女声", "language": "中文", "style": "女声"},
        {"id": "default_en", "name": "MiMo 英文女声", "language": "英文", "style": "女声"},
    ],
    "custom_tts": [
        {"id": "custom", "name": "自定义 voice id", "language": "自定义", "style": "手动填写"},
    ],
}


def _audio_format_for_preview(provider_type: str, settings: dict[str, Any]) -> str:
    """读取试听文件格式，保持和配音引擎一致"""
    if provider_type == "gemini_tts":
        return "wav"
    if provider_type == "xiaomi_mimo_tts":
        value = str(settings.get("format") or "wav").lower()
        return value if value in {"wav", "pcm16"} else "wav"
    value = str(settings.get("format") or "mp3").lower()
    return value if value in {"mp3", "wav", "flac", "pcm", "opus"} else "mp3"


def _preview_output_path(provider_type: str, settings: dict[str, Any]) -> str:
    """生成不会互相覆盖的试听输出路径"""
    import uuid
    import tempfile

    audio_format = _audio_format_for_preview(provider_type, settings)
    return os.path.join(tempfile.gettempdir(), f"youtube_voice_preview_{uuid.uuid4().hex}.{audio_format}")


def _requires_voice_api_key(provider_type: str) -> bool:
    """判断试听时是否必须有 API Key"""
    return provider_type in {"openai_tts", "gemini_tts", "minimax_tts", "xiaomi_mimo_tts"}


def _load_profile_settings(profile: VoiceProviderProfile) -> dict[str, Any]:
    """读取已保存配音配置中的高级参数"""
    if not profile.extra_params:
        return {}
    try:
        return json.loads(profile.extra_params)
    except json.JSONDecodeError:
        return {}


def _get_voice_profile(profile_id: Optional[int], db: Session) -> Optional[VoiceProviderProfile]:
    """按需读取已保存的配音配置"""
    if not profile_id:
        return None
    profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")
    return profile


def _resolve_preview_api_key(request_key: Optional[str], profile: Optional[VoiceProviderProfile]) -> str:
    """优先使用当前表单密钥，留空时使用已保存密钥"""
    api_key = request_key or ""
    if api_key.strip():
        return api_key
    if profile:
        return decrypt_api_key(profile.api_key_encrypted)
    return ""


@router.post("/voices")
async def get_voice_catalog(request: VoiceCatalogRequest):
    """获取内置音色目录"""
    return {"voices": VOICE_CATALOGS.get(request.provider_type, VOICE_CATALOGS["custom_tts"])}


@router.post("/generate")
async def generate_voice(request: VoiceGenerateRequest, db: Session = Depends(get_db)):
    """生成配音音频"""
    # 获取配音配置
    profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")

    # 解密 API 密钥
    api_key = decrypt_api_key(profile.api_key_encrypted)

    # 生成配音
    engine = VoiceEngine()
    profile_params = {}
    if profile.extra_params:
        try:
            profile_params = json.loads(profile.extra_params)
        except json.JSONDecodeError:
            profile_params = {}

    settings = {**profile_params, **request.settings}
    try:
        output_path = await engine.generate_voice(
            text=request.text,
            output_path=request.output_path,
            provider_type=profile.provider_type,
            voice=request.voice or profile.voice or "alloy",
            api_key=api_key,
            base_url=profile.base_url,
            model=request.model or profile.voice or "",
            settings=settings,
        )
        return {"message": "配音生成成功", "output_path": output_path, "audio_url": f"/voice/audio?path={quote(output_path)}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
async def preview_voice(request: VoicePreviewRequest, db: Session = Depends(get_db)):
    """使用当前表单生成试听音频，配置未保存也可以试听"""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="请输入试听文本")

    profile = _get_voice_profile(request.profile_id, db)
    provider_type = request.provider_type or (profile.provider_type if profile else "openai_tts")
    base_url = request.base_url or (profile.base_url if profile else "")
    if not base_url.strip():
        raise HTTPException(status_code=400, detail="请填写 Base URL")

    api_key = _resolve_preview_api_key(request.api_key, profile)
    if _requires_voice_api_key(provider_type) and not api_key.strip():
        raise HTTPException(status_code=400, detail="请填写 API Key，或选择已保存配音配置")

    profile_settings = _load_profile_settings(profile) if profile else {}
    settings = {**profile_settings, **request.settings}
    output_path = _preview_output_path(provider_type, settings)
    engine = VoiceEngine()

    try:
        output_path = await engine.generate_voice(
            text=request.text,
            output_path=output_path,
            provider_type=provider_type,
            voice=request.voice or settings.get("voice") or "alloy",
            api_key=api_key,
            base_url=base_url,
            model=request.model or (profile.voice if profile else ""),
            settings=settings,
        )
        return {"message": "试听音频已生成", "output_path": output_path, "audio_url": f"/voice/audio?path={quote(output_path)}"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"试听生成失败: {e}")


@router.get("/audio")
async def get_audio(path: str):
    """读取本地试听音频"""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(path)
