# backend/api/subtitles.py
# 字幕 API 路由 - 提供字幕处理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os

from ..core import Downloader, FFmpegProcessor, SubtitleEngine, TextEngine
from ..core.paths import ensure_project_dirs
from ..models import DownloadTask, SubtitlePreset, TextProviderProfile, VideoSource, get_db
from ..utils import decrypt_api_key

# 创建路由器
router = APIRouter(prefix="/subtitles", tags=["subtitles"])


class SubtitlePresetCreate(BaseModel):
    """创建字幕预设请求"""
    name: str
    is_default: bool = False
    line_mode: str = "double"
    language: str = "auto"
    font_name: str = "Microsoft YaHei"
    font_size: int = 48
    font_color: str = "#FFFFFF"
    secondary_color: str = "#FDE68A"
    outline_color: str = "#000000"
    outline_width: int = 2
    shadow_enabled: bool = True
    shadow_color: str = "#80000000"
    shadow_x: int = 2
    shadow_y: int = 2
    background_alpha: int = 0
    position: str = "bottom"
    margin_v: int = 30


class SubtitlePresetResponse(BaseModel):
    """字幕预设响应"""
    id: int
    name: str
    is_default: bool
    line_mode: str
    language: str
    font_name: str
    font_size: int
    font_color: str
    secondary_color: str
    outline_color: str
    outline_width: int
    shadow_enabled: bool
    shadow_color: str
    shadow_x: int
    shadow_y: int
    background_alpha: int
    position: str
    margin_v: int

    class Config:
        from_attributes = True


class SubtitleRenderRequest(BaseModel):
    """字幕渲染请求"""
    video_id: int
    video_path: str
    preset_id: Optional[int] = None
    language: Optional[str] = None
    sub_type: str = "auto"
    burn_in: bool = True
    subtitle_path: Optional[str] = None
    output_path: Optional[str] = None


class SubtitleRenderResponse(BaseModel):
    """字幕渲染响应"""
    message: str
    task_id: int
    subtitle_path: str
    ass_path: str
    output_path: Optional[str] = None
    plain_text: str = ""


class SubtitleTextProcessRequest(BaseModel):
    """字幕文本处理请求"""
    text: str
    profile_id: int
    operation: str = "polish"
    target_language: Optional[str] = None


class SubtitleTextProcessResponse(BaseModel):
    """字幕文本处理响应"""
    message: str
    text: str
    operation: str


def _preset_to_dict(preset: SubtitlePreset | None) -> dict:
    """将字幕预设模型转换为渲染配置"""
    if not preset:
        return {}
    return {
        "name": preset.name,
        "line_mode": preset.line_mode,
        "language": preset.language,
        "font_name": preset.font_name,
        "font_size": preset.font_size,
        "font_color": preset.font_color,
        "secondary_color": preset.secondary_color,
        "outline_color": preset.outline_color,
        "outline_width": preset.outline_width,
        "shadow_enabled": preset.shadow_enabled,
        "shadow_color": preset.shadow_color,
        "shadow_x": preset.shadow_x,
        "shadow_y": preset.shadow_y,
        "background_alpha": preset.background_alpha,
        "position": preset.position,
        "margin_v": preset.margin_v,
    }


def _pick_subtitle_preset(db: Session, preset_id: Optional[int]) -> SubtitlePreset | None:
    """选择指定或默认字幕预设"""
    if preset_id:
        preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
        if not preset:
            raise HTTPException(status_code=404, detail="字幕预设不存在")
        return preset
    return db.query(SubtitlePreset).filter(SubtitlePreset.is_default == True).first() or db.query(SubtitlePreset).first()


def _parse_subtitle_entries(engine: SubtitleEngine, subtitle_path: str) -> list[dict]:
    """按字幕文件格式解析字幕条目"""
    ext = os.path.splitext(subtitle_path)[1].lower()
    if ext == ".srt":
        return engine.parse_srt(subtitle_path)
    if ext == ".vtt":
        return engine.parse_vtt(subtitle_path)
    raise HTTPException(status_code=400, detail=f"暂不支持的字幕格式: {ext}")


def entries_to_plain_text(entries: list[dict], max_chars: int = 6000) -> str:
    """将字幕条目转换成适合配音或文本 API 处理的纯文本"""
    lines: list[str] = []
    previous = ""
    for entry in entries:
        text = str(entry.get("text", "")).replace("\\N", "\n")
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if not text or text == previous:
            continue
        lines.append(text)
        previous = text
        if sum(len(line) for line in lines) >= max_chars:
            break

    return "\n".join(lines)[:max_chars]


def _load_text_settings(profile: TextProviderProfile) -> dict:
    """读取文本 API 配置中的生成参数"""
    if not profile.extra_params:
        return {}
    import json

    try:
        data = json.loads(profile.extra_params)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.get("/presets", response_model=list[SubtitlePresetResponse])
async def get_presets(db: Session = Depends(get_db)):
    """获取所有字幕预设"""
    return db.query(SubtitlePreset).all()


@router.post("/presets", response_model=SubtitlePresetResponse)
async def create_preset(preset: SubtitlePresetCreate, db: Session = Depends(get_db)):
    """创建字幕预设"""
    db_preset = SubtitlePreset(**preset.model_dump())
    db.add(db_preset)
    db.commit()
    db.refresh(db_preset)
    return db_preset


@router.put("/presets/{preset_id}", response_model=SubtitlePresetResponse)
async def update_preset(preset_id: int, preset: SubtitlePresetCreate, db: Session = Depends(get_db)):
    """更新字幕预设"""
    db_preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
    if not db_preset:
        raise HTTPException(status_code=404, detail="预设不存在")

    for key, value in preset.model_dump().items():
        setattr(db_preset, key, value)
    db.commit()
    db.refresh(db_preset)
    return db_preset


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, db: Session = Depends(get_db)):
    """删除字幕预设"""
    preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    db.delete(preset)
    db.commit()
    return {"message": "预设已删除"}


@router.post("/process-text", response_model=SubtitleTextProcessResponse)
async def process_subtitle_text(request: SubtitleTextProcessRequest, db: Session = Depends(get_db)):
    """使用已保存文本 API 生成、翻译或润色字幕文本"""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="字幕文本不能为空")

    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")

    try:
        processed = await TextEngine().process_text(
            text=request.text,
            provider_type=profile.provider_type,
            api_key=decrypt_api_key(profile.api_key_encrypted),
            base_url=profile.base_url,
            model=profile.model or "",
            settings=_load_text_settings(profile),
            operation=request.operation,
            target_language=request.target_language or "",
        )
        return SubtitleTextProcessResponse(
            message="字幕文本处理完成",
            text=processed,
            operation=request.operation,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/render", response_model=SubtitleRenderResponse)
async def render_subtitles(request: SubtitleRenderRequest, db: Session = Depends(get_db)):
    """下载或读取字幕，生成 ASS，并可烧录成硬字幕视频"""
    video = db.query(VideoSource).filter(VideoSource.id == request.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频记录不存在")
    if not os.path.exists(request.video_path):
        raise HTTPException(status_code=404, detail="视频文件不存在")

    preset = _pick_subtitle_preset(db, request.preset_id)
    preset_dict = _preset_to_dict(preset)
    language = request.language or preset_dict.get("language") or "en"
    if language == "auto":
        language = "en"

    task = DownloadTask(
        video_id=video.id,
        task_type="subtitle",
        status="processing",
        progress=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        paths = ensure_project_dirs()
        subtitle_path = request.subtitle_path
        if not subtitle_path:
            subtitle_path = Downloader().download_subtitle(
                url=video.url,
                language=language,
                output_dir=paths["output_dir"],
                sub_type=request.sub_type,
            )
        if not subtitle_path or not os.path.exists(subtitle_path):
            raise FileNotFoundError("字幕文件不存在")

        engine = SubtitleEngine()
        entries = _parse_subtitle_entries(engine, subtitle_path)
        if not entries:
            raise RuntimeError("字幕文件为空，无法生成 ASS")

        base_name = os.path.splitext(os.path.basename(request.video_path))[0]
        ass_path = os.path.join(paths["output_dir"], f"{base_name}_{language}.ass")
        engine.generate_ass(entries, ass_path, preset_dict)

        output_path = None
        if request.burn_in:
            output_path = FFmpegProcessor().burn_subtitles(
                video_path=request.video_path,
                subtitle_path=ass_path,
                output_path=request.output_path,
                preset=preset_dict,
            )

        plain_text = entries_to_plain_text(entries)
        task.status = "completed"
        task.progress = 100
        task.output_path = output_path or ass_path
        db.commit()
        return SubtitleRenderResponse(
            message="字幕处理完成",
            task_id=task.id,
            subtitle_path=subtitle_path,
            ass_path=ass_path,
            output_path=output_path,
            plain_text=plain_text,
        )
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
