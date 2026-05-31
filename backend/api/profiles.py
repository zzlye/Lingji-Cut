# backend/api/profiles.py
# API 配置路由 - 提供 API 配置管理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..models import get_db, TextProviderProfile, VoiceProviderProfile
from ..utils import encrypt_api_key, decrypt_api_key

# 创建路由器
router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileCreate(BaseModel):
    """创建配置请求"""
    name: str
    provider_type: str
    base_url: str
    api_key: str
    model: Optional[str] = None
    extra_params: Optional[str] = None


class ProfileResponse(BaseModel):
    """配置响应（不包含 api_key）"""
    id: int
    name: str
    provider_type: str
    base_url: str
    model: Optional[str] = None
    extra_params: Optional[str] = None

    class Config:
        from_attributes = True


def _voice_profile_to_response(profile: VoiceProviderProfile) -> ProfileResponse:
    """将配音配置模型转换为前端响应"""
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        provider_type=profile.provider_type,
        base_url=profile.base_url,
        model=profile.voice,
        extra_params=profile.extra_params,
    )


@router.get("/text", response_model=list[ProfileResponse])
async def get_text_profiles(db: Session = Depends(get_db)):
    """获取所有文本 API 配置"""
    return db.query(TextProviderProfile).all()


@router.post("/text", response_model=ProfileResponse)
async def create_text_profile(profile: ProfileCreate, db: Session = Depends(get_db)):
    """创建文本 API 配置"""
    encrypted_key = encrypt_api_key(profile.api_key)
    db_profile = TextProviderProfile(
        name=profile.name,
        provider_type=profile.provider_type,
        base_url=profile.base_url,
        api_key_encrypted=encrypted_key,
        model=profile.model,
        extra_params=profile.extra_params,
    )
    db.add(db_profile)
    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.get("/voice", response_model=list[ProfileResponse])
async def get_voice_profiles(db: Session = Depends(get_db)):
    """获取所有配音 API 配置"""
    profiles = db.query(VoiceProviderProfile).all()
    return [_voice_profile_to_response(profile) for profile in profiles]


@router.post("/voice", response_model=ProfileResponse)
async def create_voice_profile(profile: ProfileCreate, db: Session = Depends(get_db)):
    """创建配音 API 配置"""
    encrypted_key = encrypt_api_key(profile.api_key)
    db_profile = VoiceProviderProfile(
        name=profile.name,
        provider_type=profile.provider_type,
        base_url=profile.base_url,
        api_key_encrypted=encrypted_key,
        voice=profile.model,
        extra_params=profile.extra_params,
    )
    db.add(db_profile)
    db.commit()
    db.refresh(db_profile)
    return _voice_profile_to_response(db_profile)


@router.put("/voice/{profile_id}", response_model=ProfileResponse)
async def update_voice_profile(profile_id: int, profile: ProfileCreate, db: Session = Depends(get_db)):
    """更新配音 API 配置"""
    db_profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")

    db_profile.name = profile.name
    db_profile.provider_type = profile.provider_type
    db_profile.base_url = profile.base_url
    db_profile.api_key_encrypted = encrypt_api_key(profile.api_key)
    db_profile.voice = profile.model
    db_profile.extra_params = profile.extra_params
    db.commit()
    db.refresh(db_profile)
    return _voice_profile_to_response(db_profile)


@router.post("/test/{profile_type}/{profile_id}")
async def test_profile(profile_type: str, profile_id: int, db: Session = Depends(get_db)):
    """测试 API 配置连接"""
    if profile_type == "text":
        profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == profile_id).first()
    elif profile_type == "voice":
        profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    else:
        raise HTTPException(status_code=400, detail="无效的配置类型")

    if not profile:
        raise HTTPException(status_code=404, detail="配置不存在")

    # TODO: 实际测试 API 连接
    api_key = decrypt_api_key(profile.api_key_encrypted)
    return {"message": "连接测试成功", "status": "ok"}
