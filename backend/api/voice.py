# backend/api/voice.py
# 配音 API 路由 - 提供配音生成接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

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
    output_path: Optional[str] = None


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
    try:
        output_path = await engine.generate_voice(
            text=request.text,
            output_path=request.output_path,
            provider_type=profile.provider_type,
            voice=request.voice or profile.voice or "alloy",
            api_key=api_key,
            base_url=profile.base_url,
        )
        return {"message": "配音生成成功", "output_path": output_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
