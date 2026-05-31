# backend/api/subtitles.py
# 字幕 API 路由 - 提供字幕处理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..models import get_db, SubtitlePreset

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
