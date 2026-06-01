# backend/api/profiles.py
# API 配置路由 - 提供 API 配置管理接口

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models import get_db, TextProviderProfile, VoiceProviderProfile
from ..utils import encrypt_api_key, decrypt_api_key

# 创建路由器
router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileCreate(BaseModel):
    """创建配置请求"""
    name: str
    provider_type: str
    base_url: str
    api_key: str = ""
    model: Optional[str] = None
    extra_params: Optional[str] = None


class ProfileUpdate(BaseModel):
    """更新配置请求，API Key 留空时保留旧密钥"""
    name: str
    provider_type: str
    base_url: str
    api_key: Optional[str] = None
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


class TextModelListRequest(BaseModel):
    """文本模型列表请求"""
    provider_type: str
    base_url: str
    api_key: Optional[str] = None
    profile_id: Optional[int] = None


class TextModelOption(BaseModel):
    """文本模型选项"""
    id: str
    label: str
    owned_by: Optional[str] = None


class TextModelListResponse(BaseModel):
    """文本模型列表响应"""
    models: list[TextModelOption]
    source: str
    message: str


def _join_url(base_url: str, path: str) -> str:
    """拼接 API 地址，保留用户配置的版本前缀"""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _models_endpoint(base_url: str) -> str:
    """根据 Base URL 得到模型列表地址"""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/models"):
        return normalized
    return _join_url(normalized, "models")


def _auth_headers(provider_type: str, api_key: str) -> dict[str, str]:
    """根据渠道类型生成鉴权请求头"""
    headers: dict[str, str] = {}
    if provider_type == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _requires_api_key(provider_type: str) -> bool:
    """判断渠道是否必须填写 API Key 才能拉取模型"""
    return provider_type in {"openai", "gemini", "gemini_compatible", "anthropic", "minimax", "xiaomi_mimo"}


def _parse_openai_models(payload: dict) -> list[TextModelOption]:
    """解析 OpenAI / OpenAI 兼容模型列表"""
    models = []
    for item in payload.get("data", []):
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(TextModelOption(
            id=model_id,
            label=model_id,
            owned_by=item.get("owned_by"),
        ))
    return models


def _parse_gemini_models(payload: dict) -> list[TextModelOption]:
    """解析 Gemini 模型列表"""
    models = []
    for item in payload.get("models", []):
        raw_name = item.get("name", "")
        if not raw_name:
            continue
        model_id = raw_name.removeprefix("models/")
        display_name = item.get("displayName") or model_id
        models.append(TextModelOption(
            id=model_id,
            label=display_name,
            owned_by="google",
        ))
    return models


def _parse_anthropic_models(payload: dict) -> list[TextModelOption]:
    """解析 Anthropic 模型列表"""
    models = []
    for item in payload.get("data", []):
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(TextModelOption(
            id=model_id,
            label=item.get("display_name") or model_id,
            owned_by="anthropic",
        ))
    return models


async def _fetch_text_models(provider_type: str, base_url: str, api_key: str) -> list[TextModelOption]:
    """从文本模型渠道获取模型列表"""
    if not base_url.strip():
        raise HTTPException(status_code=400, detail="请填写 Base URL")
    if _requires_api_key(provider_type) and not api_key.strip():
        raise HTTPException(status_code=400, detail="请先填写 API Key 或选择已保存配置")

    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = _auth_headers(provider_type, api_key)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider_type in {"gemini", "gemini_compatible"}:
                response = await client.get(
                    _models_endpoint(base_url),
                    params={"key": api_key} if api_key else None,
                    headers={"x-goog-api-key": api_key} if api_key else None,
                )
                response.raise_for_status()
                return _parse_gemini_models(response.json())

            if provider_type == "anthropic":
                response = await client.get(_models_endpoint(base_url), headers=headers)
                response.raise_for_status()
                return _parse_anthropic_models(response.json())

            # OpenAI、OpenAI-compatible、MiniMax、小米 MiMo 和自定义渠道都先按 OpenAI 兼容格式读取。
            response = await client.get(_models_endpoint(base_url), headers=headers)
            response.raise_for_status()
            return _parse_openai_models(response.json())
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        raise HTTPException(status_code=502, detail=f"模型列表获取失败，渠道返回 HTTP {status_code}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"模型列表获取失败: {exc.__class__.__name__}") from exc


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


@router.put("/text/{profile_id}", response_model=ProfileResponse)
async def update_text_profile(profile_id: int, profile: ProfileUpdate, db: Session = Depends(get_db)):
    """更新文本 API 配置"""
    db_profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="文本配置不存在")

    db_profile.name = profile.name
    db_profile.provider_type = profile.provider_type
    db_profile.base_url = profile.base_url
    if profile.api_key is not None and profile.api_key.strip():
        db_profile.api_key_encrypted = encrypt_api_key(profile.api_key)
    db_profile.model = profile.model
    db_profile.extra_params = profile.extra_params
    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.post("/text/models", response_model=TextModelListResponse)
async def list_text_models(request: TextModelListRequest, db: Session = Depends(get_db)):
    """获取文本 API 模型列表"""
    api_key = request.api_key or ""
    if request.profile_id and not api_key.strip():
        profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="文本配置不存在")
        api_key = decrypt_api_key(profile.api_key_encrypted)

    models = await _fetch_text_models(
        provider_type=request.provider_type,
        base_url=request.base_url,
        api_key=api_key,
    )
    return TextModelListResponse(
        models=models,
        source="remote",
        message=f"已获取 {len(models)} 个模型",
    )


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
async def update_voice_profile(profile_id: int, profile: ProfileUpdate, db: Session = Depends(get_db)):
    """更新配音 API 配置"""
    db_profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")

    db_profile.name = profile.name
    db_profile.provider_type = profile.provider_type
    db_profile.base_url = profile.base_url
    if profile.api_key is not None and profile.api_key.strip():
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

    api_key = decrypt_api_key(profile.api_key_encrypted)
    if profile_type == "text":
        models = await _fetch_text_models(profile.provider_type, profile.base_url, api_key)
        return {"message": f"连接测试成功，已读取 {len(models)} 个模型", "status": "ok"}

    return {"message": "连接测试成功", "status": "ok"}
