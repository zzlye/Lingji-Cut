# backend/api/profiles.py
# API 配置路由 - 提供 API 配置管理接口

import json
import os
import tempfile
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import VoiceEngine
from ..core.voice_engine import provider_audio_format
from ..models import get_db, TextProviderProfile, VoiceProviderProfile
from ..utils import encrypt_api_key, decrypt_api_key
from .voice import VOICE_CATALOGS

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


class ProfileRename(BaseModel):
    """修改配置名称请求"""
    name: str


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


class ProfileSecretResponse(BaseModel):
    """按需返回已保存的 API Key，默认不出现在列表接口中"""
    api_key: str


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


class VoiceModelListRequest(BaseModel):
    """配音模型列表请求"""
    provider_type: str
    base_url: str
    api_key: Optional[str] = None
    profile_id: Optional[int] = None


class VoiceModelListResponse(BaseModel):
    """配音模型列表响应"""
    models: list[TextModelOption]
    source: str
    message: str


class VoiceProfileTestRequest(BaseModel):
    """配音配置测试请求，支持未保存表单直接测试"""
    name: str = "临时配音配置"
    provider_type: str
    base_url: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    extra_params: Optional[str] = None
    profile_id: Optional[int] = None


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


def _voice_requires_api_key(provider_type: str) -> bool:
    """判断配音渠道拉取模型时是否必须填写 API Key"""
    return provider_type in {"openai_tts", "gemini_tts", "minimax_tts", "xiaomi_mimo_tts"}


def _voice_is_local_provider(provider_type: str) -> bool:
    """判断配音渠道是否完全在本地运行，不需要远程密钥"""
    return provider_type in {"local_tts", "gpt_sovits", "index_tts2"}


def _has_saved_api_key(encrypted_key: str) -> bool:
    """判断数据库里是否真的保存了可用 API Key"""
    try:
        return bool(decrypt_api_key(encrypted_key).strip())
    except Exception:
        return False


def _require_api_key_for_create(api_key: str, label: str) -> None:
    """新建配置必须写入密钥，避免保存出看似可用的空配置"""
    if not str(api_key or "").strip():
        raise HTTPException(status_code=400, detail=f"新建{label}配置需要填写 API Key")


def _require_api_key_for_update(existing_encrypted_key: str, label: str) -> None:
    """更新旧配置时允许留空保留旧密钥，但旧密钥为空时必须补填"""
    if not _has_saved_api_key(existing_encrypted_key):
        raise HTTPException(status_code=400, detail=f"当前{label}配置还没有 API Key，请填写后再保存")


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


def _default_voice_models(provider_type: str) -> list[TextModelOption]:
    """返回配音渠道内置模型，远程列表不可用时兜底展示"""
    defaults = {
        "openai_tts": [
            ("gpt-4o-mini-tts", "gpt-4o-mini-tts"),
            ("tts-1", "tts-1"),
            ("tts-1-hd", "tts-1-hd"),
        ],
        "gemini_tts": [
            ("gemini-2.5-flash-preview-tts", "gemini-2.5-flash-preview-tts"),
            ("gemini-2.5-pro-preview-tts", "gemini-2.5-pro-preview-tts"),
        ],
        "minimax_tts": [
            ("speech-2.8-hd", "speech-2.8-hd"),
            ("speech-2.8-turbo", "speech-2.8-turbo"),
            ("speech-2.6-hd", "speech-2.6-hd"),
            ("speech-2.6-turbo", "speech-2.6-turbo"),
            ("speech-02-hd", "speech-02-hd"),
            ("speech-02-turbo", "speech-02-turbo"),
        ],
        "xiaomi_mimo_tts": [
            ("mimo-v2.5-tts", "mimo-v2.5-tts"),
            ("mimo-v2.5-tts-voiceclone", "mimo-v2.5-tts-voiceclone"),
            ("mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voicedesign"),
            ("mimo-v2-tts", "mimo-v2-tts"),
        ],
        "local_tts": [
            ("local-command", "本地命令"),
        ],
        "gpt_sovits": [
            ("gpt-sovits-v2", "GPT-SoVITS 本地服务"),
            ("gpt-sovits-v3", "GPT-SoVITS V3/V4 本地服务"),
        ],
        "index_tts2": [
            ("index-tts2", "IndexTTS2 本地模型"),
        ],
        "custom_tts": [],
    }
    return [TextModelOption(id=model_id, label=label, owned_by=provider_type) for model_id, label in defaults.get(provider_type, [])]


def _filter_voice_models(provider_type: str, models: list[TextModelOption]) -> list[TextModelOption]:
    """从通用模型列表中过滤出更可能用于 TTS 的模型"""
    if provider_type == "custom_tts":
        return models
    keywords = {
        "openai_tts": ["tts", "audio", "speech"],
        "gemini_tts": ["tts", "speech", "preview-tts"],
        "minimax_tts": ["speech", "tts", "voice"],
        "xiaomi_mimo_tts": ["tts", "audio", "speech", "mimo"],
    }.get(provider_type, [])
    if not keywords:
        return models
    filtered = [
        model for model in models
        if any(keyword in model.id.lower() or keyword in model.label.lower() for keyword in keywords)
    ]
    return filtered or models


async def _fetch_voice_models(provider_type: str, base_url: str, api_key: str) -> tuple[list[TextModelOption], str]:
    """获取配音模型列表，优先远程读取，失败时返回内置模型"""
    defaults = _default_voice_models(provider_type)
    if _voice_is_local_provider(provider_type):
        return defaults, "local"

    if not base_url.strip():
        raise HTTPException(status_code=400, detail="请填写 Base URL")

    if _voice_requires_api_key(provider_type) and not api_key.strip():
        if defaults:
            return defaults, "local"
        raise HTTPException(status_code=400, detail="请先填写 API Key 或选择已保存配置")

    if provider_type == "custom_tts" and not api_key.strip():
        return defaults, "local"

    # MiniMax 和小米 TTS 官方文档直接公布固定模型表，未提供稳定的 /models 拉取接口。
    if provider_type in {"minimax_tts", "xiaomi_mimo_tts"}:
        return defaults, "local"

    try:
        if provider_type == "gemini_tts":
            models = await _fetch_text_models("gemini", base_url, api_key)
        elif provider_type in {"openai_tts", "minimax_tts", "xiaomi_mimo_tts", "custom_tts"}:
            models = await _fetch_text_models("openai_compatible", base_url, api_key)
        else:
            models = []
        filtered = _filter_voice_models(provider_type, models)
        return (filtered or defaults), "remote" if filtered else "local"
    except Exception:
        if defaults:
            return defaults, "local"
        raise


async def _test_voice_profile(profile: VoiceProviderProfile, api_key: str) -> None:
    """通过生成极短试听音频真实测试配音配置"""
    settings = {}
    if profile.extra_params:
        try:
            settings = json.loads(profile.extra_params)
        except json.JSONDecodeError:
            settings = {}

    base_url = profile.base_url
    if profile.provider_type == "local_tts":
        command_template = str(settings.get("local_tts_command") or profile.base_url or "").strip()
        if not command_template:
            raise HTTPException(status_code=400, detail="请填写本地 TTS 命令模板")
    elif profile.provider_type == "gpt_sovits":
        base_url = profile.base_url.strip() or "http://127.0.0.1:9880"
    elif profile.provider_type == "index_tts2":
        base_url = str(settings.get("index_tts2_repo_dir") or profile.base_url or "").strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="请填写 IndexTTS2 项目目录")
        voice_value = str(settings.get("voice") or "").strip()
        speaker_audio = str(settings.get("index_tts2_speaker_audio_path") or "").strip()
        if voice_value.startswith("index_tts2_ref:"):
            speaker_audio = voice_value.split(":", 1)[1].strip() or speaker_audio
        if not speaker_audio:
            raise HTTPException(status_code=400, detail="请填写 IndexTTS2 发音参考音频")
    elif not profile.base_url.strip():
        raise HTTPException(status_code=400, detail="请填写 Base URL")
    if _voice_requires_api_key(profile.provider_type) and not api_key.strip():
        raise HTTPException(status_code=400, detail="请填写 API Key，或选择已保存配音配置")

    voice = settings.get("voice") or _default_voice_id(profile.provider_type)
    model = profile.voice or _default_voice_model(profile.provider_type)
    audio_format = provider_audio_format(
        VoiceEngine.resolve_provider_type(profile.provider_type, model),
        settings,
        model,
    )
    output_path = os.path.join(tempfile.gettempdir(), f"youtube_voice_test_{profile.id}.{audio_format}")
    engine = VoiceEngine()
    await engine.generate_voice(
        text="配音连接测试。",
        output_path=output_path,
        provider_type=profile.provider_type,
        voice=voice,
        api_key=api_key,
        base_url=base_url,
        model=model,
        settings=settings,
    )
    if os.path.exists(output_path):
        os.remove(output_path)


def _default_voice_id(provider_type: str) -> str:
    """按渠道返回默认音色 ID"""
    voices = VOICE_CATALOGS.get(provider_type) or VOICE_CATALOGS["custom_tts"]
    return str(voices[0]["id"]) if voices else "alloy"


def _default_voice_model(provider_type: str) -> str:
    """按渠道返回默认配音模型"""
    models = _default_voice_models(provider_type)
    return models[0].id if models else ""


def _transient_voice_profile(request: VoiceProfileTestRequest, saved_profile: Optional[VoiceProviderProfile]) -> VoiceProviderProfile:
    """把未保存表单转换成可复用的临时配音配置对象"""
    return VoiceProviderProfile(
        id=saved_profile.id if saved_profile else 0,
        name=request.name or (saved_profile.name if saved_profile else "临时配音配置"),
        provider_type=request.provider_type or (saved_profile.provider_type if saved_profile else "openai_tts"),
        base_url=request.base_url or (saved_profile.base_url if saved_profile else ""),
        voice=request.model or (saved_profile.voice if saved_profile else ""),
        extra_params=request.extra_params or (saved_profile.extra_params if saved_profile else None),
    )


def _resolve_saved_voice_profile(profile_id: Optional[int], db: Session) -> Optional[VoiceProviderProfile]:
    """按需读取已保存的配音配置"""
    if not profile_id:
        return None
    profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")
    return profile


def _resolve_voice_api_key(request_key: Optional[str], saved_profile: Optional[VoiceProviderProfile]) -> str:
    """优先使用表单密钥，留空时回退到已保存密钥"""
    api_key = request_key or ""
    if api_key.strip():
        return api_key
    if saved_profile:
        return decrypt_api_key(saved_profile.api_key_encrypted)
    return ""


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


def _get_text_profile_or_404(profile_id: int, db: Session) -> TextProviderProfile:
    """读取文本配置，不存在时抛出 404"""
    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本配置不存在")
    return profile


def _get_voice_profile_or_404(profile_id: int, db: Session) -> VoiceProviderProfile:
    """读取配音配置，不存在时抛出 404"""
    profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="配音配置不存在")
    return profile


@router.get("/text", response_model=list[ProfileResponse])
async def get_text_profiles(db: Session = Depends(get_db)):
    """获取所有文本 API 配置"""
    return db.query(TextProviderProfile).all()


@router.get("/text/{profile_id}/secret", response_model=ProfileSecretResponse)
async def get_text_profile_secret(profile_id: int, db: Session = Depends(get_db)):
    """按需读取已保存的文本 API Key，避免列表接口直接暴露密钥"""
    profile = _get_text_profile_or_404(profile_id, db)
    return ProfileSecretResponse(api_key=decrypt_api_key(profile.api_key_encrypted))


@router.post("/text", response_model=ProfileResponse)
async def create_text_profile(profile: ProfileCreate, db: Session = Depends(get_db)):
    """创建文本 API 配置"""
    _require_api_key_for_create(profile.api_key, "文本 API")
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
    db_profile = _get_text_profile_or_404(profile_id, db)

    db_profile.name = profile.name
    db_profile.provider_type = profile.provider_type
    db_profile.base_url = profile.base_url
    if profile.api_key is not None and profile.api_key.strip():
        db_profile.api_key_encrypted = encrypt_api_key(profile.api_key)
    else:
        _require_api_key_for_update(db_profile.api_key_encrypted, "文本 API")
    db_profile.model = profile.model
    db_profile.extra_params = profile.extra_params
    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.patch("/text/{profile_id}/name", response_model=ProfileResponse)
async def rename_text_profile(profile_id: int, request: ProfileRename, db: Session = Depends(get_db)):
    """只修改文本 API 配置名称，不触碰密钥、渠道和模型"""
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="配置名称不能为空")
    db_profile = _get_text_profile_or_404(profile_id, db)
    db_profile.name = name
    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.delete("/text/{profile_id}")
async def delete_text_profile(profile_id: int, db: Session = Depends(get_db)):
    """删除文本 API 配置"""
    db_profile = _get_text_profile_or_404(profile_id, db)
    db.delete(db_profile)
    db.commit()
    return {"message": "文本 API 配置已删除", "profile_id": profile_id}


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


@router.post("/voice/models", response_model=VoiceModelListResponse)
async def list_voice_models(request: VoiceModelListRequest, db: Session = Depends(get_db)):
    """获取配音 API 模型列表"""
    api_key = request.api_key or ""
    if request.profile_id and not api_key.strip():
        profile = _resolve_saved_voice_profile(request.profile_id, db)
        api_key = decrypt_api_key(profile.api_key_encrypted) if profile else ""

    models, source = await _fetch_voice_models(
        provider_type=request.provider_type,
        base_url=request.base_url,
        api_key=api_key,
    )
    source_label = "远程" if source == "remote" else "内置"
    if source == "local" and _voice_requires_api_key(request.provider_type) and not api_key.strip():
        message = f"未填写 API Key，已显示 {len(models)} 个内置配音模型；填写密钥后可读取远程模型"
    elif source == "local" and len(models) == 0:
        message = "当前渠道没有内置模型，请手动填写模型名称"
    else:
        message = f"已获取 {len(models)} 个{source_label}配音模型"
    return VoiceModelListResponse(
        models=models,
        source=source,
        message=message,
    )


@router.post("/voice/test")
async def test_voice_profile_from_form(request: VoiceProfileTestRequest, db: Session = Depends(get_db)):
    """测试当前配音表单，未保存配置也可以直接测试"""
    saved_profile = _resolve_saved_voice_profile(request.profile_id, db)
    api_key = _resolve_voice_api_key(request.api_key, saved_profile)
    profile = _transient_voice_profile(request, saved_profile)

    try:
        await _test_voice_profile(profile, api_key)
        return {"message": "连接测试成功，已生成短试听音频", "status": "ok"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"配音连接测试失败: {exc}") from exc


@router.get("/voice", response_model=list[ProfileResponse])
async def get_voice_profiles(db: Session = Depends(get_db)):
    """获取所有配音 API 配置"""
    profiles = db.query(VoiceProviderProfile).all()
    return [_voice_profile_to_response(profile) for profile in profiles]


@router.get("/voice/{profile_id}/secret", response_model=ProfileSecretResponse)
async def get_voice_profile_secret(profile_id: int, db: Session = Depends(get_db)):
    """按需读取已保存的配音 API Key，默认仍保持隐藏"""
    profile = _get_voice_profile_or_404(profile_id, db)
    return ProfileSecretResponse(api_key=decrypt_api_key(profile.api_key_encrypted))


@router.post("/voice", response_model=ProfileResponse)
async def create_voice_profile(profile: ProfileCreate, db: Session = Depends(get_db)):
    """创建配音 API 配置"""
    if not _voice_is_local_provider(profile.provider_type):
        _require_api_key_for_create(profile.api_key, "配音 API")
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
    db_profile = _get_voice_profile_or_404(profile_id, db)

    db_profile.name = profile.name
    db_profile.provider_type = profile.provider_type
    db_profile.base_url = profile.base_url
    if profile.api_key is not None and profile.api_key.strip():
        db_profile.api_key_encrypted = encrypt_api_key(profile.api_key)
    elif not _voice_is_local_provider(profile.provider_type):
        _require_api_key_for_update(db_profile.api_key_encrypted, "配音 API")
    else:
        db_profile.api_key_encrypted = encrypt_api_key("")
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

    await _test_voice_profile(profile, api_key)
    return {"message": "连接测试成功，已生成试听音频", "status": "ok"}
