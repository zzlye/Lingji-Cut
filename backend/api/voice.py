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
from ..core.voice_engine import provider_audio_format

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
    model: Optional[str] = None


VOICE_CATALOGS = {
    "openai_tts": [
        {"id": "alloy", "name": "Alloy", "language": "多语言", "style": "中性、均衡、通用", "gender": "neutral"},
        {"id": "ash", "name": "Ash", "language": "多语言", "style": "偏男、稳重、叙述", "gender": "male"},
        {"id": "ballad", "name": "Ballad", "language": "多语言", "style": "中性、柔和、故事感", "gender": "neutral"},
        {"id": "coral", "name": "Coral", "language": "多语言", "style": "偏女、清晰、亲和", "gender": "female"},
        {"id": "echo", "name": "Echo", "language": "多语言", "style": "偏男、清楚", "gender": "male"},
        {"id": "fable", "name": "Fable", "language": "多语言", "style": "中性、表达、叙事", "gender": "neutral"},
        {"id": "nova", "name": "Nova", "language": "多语言", "style": "偏女、自然", "gender": "female"},
        {"id": "onyx", "name": "Onyx", "language": "多语言", "style": "偏男、低沉、沉稳", "gender": "male"},
        {"id": "sage", "name": "Sage", "language": "多语言", "style": "偏女、成熟、知识感", "gender": "female"},
        {"id": "shimmer", "name": "Shimmer", "language": "多语言", "style": "偏女、明亮、轻快", "gender": "female"},
    ],
    "gemini_tts": [
        {"id": "Kore", "name": "Kore", "language": "多语言", "style": "偏女、清晰", "gender": "female"},
        {"id": "Puck", "name": "Puck", "language": "多语言", "style": "偏男、活泼", "gender": "male"},
        {"id": "Charon", "name": "Charon", "language": "多语言", "style": "偏男、沉稳", "gender": "male"},
        {"id": "Fenrir", "name": "Fenrir", "language": "多语言", "style": "偏男、厚重", "gender": "male"},
        {"id": "Aoede", "name": "Aoede", "language": "多语言", "style": "偏女、柔和", "gender": "female"},
        {"id": "Leda", "name": "Leda", "language": "多语言", "style": "偏女、明亮", "gender": "female"},
        {"id": "Orus", "name": "Orus", "language": "多语言", "style": "偏男、正式", "gender": "male"},
        {"id": "Zephyr", "name": "Zephyr", "language": "多语言", "style": "偏女、轻快", "gender": "female"},
        {"id": "Achernar", "name": "Achernar", "language": "多语言", "style": "偏女、柔和", "gender": "female"},
        {"id": "Achird", "name": "Achird", "language": "多语言", "style": "中性、亲切", "gender": "neutral"},
        {"id": "Algenib", "name": "Algenib", "language": "多语言", "style": "偏男、沙哑", "gender": "male"},
        {"id": "Algieba", "name": "Algieba", "language": "多语言", "style": "偏男、平滑", "gender": "male"},
        {"id": "Alnilam", "name": "Alnilam", "language": "多语言", "style": "偏男、坚定", "gender": "male"},
        {"id": "Autonoe", "name": "Autonoe", "language": "多语言", "style": "偏女、明亮", "gender": "female"},
        {"id": "Callirrhoe", "name": "Callirrhoe", "language": "多语言", "style": "偏女、轻松", "gender": "female"},
        {"id": "Despina", "name": "Despina", "language": "多语言", "style": "偏女、平滑", "gender": "female"},
        {"id": "Enceladus", "name": "Enceladus", "language": "多语言", "style": "偏男、气息感", "gender": "male"},
        {"id": "Erinome", "name": "Erinome", "language": "多语言", "style": "偏女、清亮", "gender": "female"},
        {"id": "Gacrux", "name": "Gacrux", "language": "多语言", "style": "偏女、成熟", "gender": "female"},
        {"id": "Iapetus", "name": "Iapetus", "language": "多语言", "style": "偏男、清澈", "gender": "male"},
        {"id": "Laomedeia", "name": "Laomedeia", "language": "多语言", "style": "偏女、轻快", "gender": "female"},
        {"id": "Pulcherrima", "name": "Pulcherrima", "language": "多语言", "style": "偏女、向前感", "gender": "female"},
        {"id": "Rasalgethi", "name": "Rasalgethi", "language": "多语言", "style": "偏男、信息感", "gender": "male"},
        {"id": "Sadachbia", "name": "Sadachbia", "language": "多语言", "style": "偏男、活泼", "gender": "male"},
        {"id": "Sadaltager", "name": "Sadaltager", "language": "多语言", "style": "偏男、知识感", "gender": "male"},
        {"id": "Schedar", "name": "Schedar", "language": "多语言", "style": "中性、均衡", "gender": "neutral"},
        {"id": "Sulafat", "name": "Sulafat", "language": "多语言", "style": "偏女、温暖", "gender": "female"},
        {"id": "Umbriel", "name": "Umbriel", "language": "多语言", "style": "偏男、轻松", "gender": "male"},
        {"id": "Vindemiatrix", "name": "Vindemiatrix", "language": "多语言", "style": "偏女、温和", "gender": "female"},
        {"id": "Zubenelgenubi", "name": "Zubenelgenubi", "language": "多语言", "style": "偏男、随性", "gender": "male"},
    ],
    "minimax_tts": [
        {"id": "Chinese_Professional_Male", "name": "中文专业男声", "language": "中文", "style": "男声、商业解说", "gender": "male"},
        {"id": "Chinese_Professional_Female", "name": "中文专业女声", "language": "中文", "style": "女声、商业解说", "gender": "female"},
        {"id": "Chinese_Gentleman", "name": "中文绅士男声", "language": "中文", "style": "男声、沉稳", "gender": "male"},
        {"id": "Chinese_Graceful_Lady", "name": "中文优雅女声", "language": "中文", "style": "女声、自然", "gender": "female"},
        {"id": "English_expressive_narrator", "name": "English Expressive Narrator", "language": "英文", "style": "中性、叙事", "gender": "neutral"},
        {"id": "English_Graceful_Lady", "name": "English Graceful Lady", "language": "英文", "style": "女声、优雅", "gender": "female"},
    ],
    "xiaomi_mimo_tts": [
        {"id": "mimo_default", "name": "MiMo 默认", "language": "中文/英文", "style": "中性、默认", "gender": "neutral"},
        {"id": "default_zh", "name": "MiMo 中文女声", "language": "中文", "style": "女声", "gender": "female"},
        {"id": "default_en", "name": "MiMo 英文女声", "language": "英文", "style": "女声", "gender": "female"},
    ],
    "custom_tts": [
        {"id": "custom", "name": "自定义 voice id", "language": "自定义", "style": "中性、手动填写", "gender": "neutral"},
    ],
}


def _audio_format_for_preview(provider_type: str, settings: dict[str, Any], model: str = "") -> str:
    """读取试听文件格式，保持和配音引擎一致"""
    return provider_audio_format(VoiceEngine.resolve_provider_type(provider_type, model), settings, model)


def _voice_catalog_key(provider_type: str, model: str = "") -> str:
    """音色目录只按用户选择的渠道展示，避免模型名隐式改协议"""
    return VoiceEngine.resolve_provider_type(provider_type, model)


def _preview_output_path(provider_type: str, settings: dict[str, Any], model: str = "") -> str:
    """生成不会互相覆盖的试听输出路径"""
    import uuid
    import tempfile

    audio_format = _audio_format_for_preview(provider_type, settings, model)
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
    catalog_key = _voice_catalog_key(request.provider_type, request.model or "")
    return {"voices": VOICE_CATALOGS.get(catalog_key, VOICE_CATALOGS["custom_tts"])}


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
    model = request.model or (profile.voice if profile else "")
    output_path = _preview_output_path(provider_type, settings, model)
    engine = VoiceEngine()

    try:
        output_path = await engine.generate_voice(
            text=request.text,
            output_path=output_path,
            provider_type=provider_type,
            voice=request.voice or settings.get("voice") or "alloy",
            api_key=api_key,
            base_url=base_url,
            model=model,
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
