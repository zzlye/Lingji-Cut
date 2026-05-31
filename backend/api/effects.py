# backend/api/effects.py
# 画面处理 API 路由 - 提供预设管理、预览和完整处理接口

import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core import FFmpegProcessor
from ..models import DownloadTask, ProcessingPreset, get_db


router = APIRouter(prefix="/effects", tags=["effects"])


class RandomRange(BaseModel):
    """固定值或随机范围配置"""
    enabled: bool = True
    random: bool = False
    value: Optional[float] = None
    min: float = 0
    max: float = 0


class AdjustmentConfig(BaseModel):
    """画面基础调整配置"""
    enabled: bool = True
    brightness: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=0.0, max=0.1))
    contrast: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=1.0, max=1.2))
    saturation: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=1.0, max=1.1))
    sharpness: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=0.9, max=1.4))
    denoise: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=1.0, max=2.0))


class CanvasConfig(BaseModel):
    """分辨率和画布配置"""
    enabled: bool = True
    resolution: Literal["720p", "1080p", "original", "custom"] = "720p"
    mode: Literal["keep", "stretch", "crop", "blur_background"] = "keep"
    width: int = 1280
    height: int = 720
    background_enabled: bool = False
    reflection_enabled: bool = False
    grid_enabled: bool = False


class TransformConfig(BaseModel):
    """旋转与翻转配置"""
    enabled: bool = True
    rotate_mode: Literal["none", "left90", "right90"] = "none"
    flip_horizontal: bool = True
    flip_vertical: bool = False
    random_rotate: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=-1.0, max=1.0))
    remove_black_bars: bool = False
    show_full_frame: bool = True


class DropFrameConfig(BaseModel):
    """抽帧配置"""
    enabled: bool = False
    interval: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=25, max=30))


class TimingConfig(BaseModel):
    """帧率与动态变化配置"""
    enabled: bool = True
    fps: RandomRange = Field(default_factory=lambda: RandomRange(random=False, value=30, min=30, max=30))
    drop_frame: DropFrameConfig = Field(default_factory=DropFrameConfig)
    dynamic_zoom: RandomRange = Field(default_factory=lambda: RandomRange(enabled=False, random=True, min=0.01, max=0.02))


class BitrateConfig(BaseModel):
    """码率与清晰度配置"""
    enabled: bool = True
    mode: Literal["fixed", "multiplier"] = "fixed"
    fixed_kbps: RandomRange = Field(default_factory=lambda: RandomRange(random=False, value=2000, min=2000, max=2000))
    multiplier: RandomRange = Field(default_factory=lambda: RandomRange(random=True, min=1.05, max=1.95))
    quality_mode: Literal["balanced", "quality", "size"] = "balanced"


class ProcessingConfig(BaseModel):
    """完整画面处理配置"""
    adjustments: AdjustmentConfig = Field(default_factory=AdjustmentConfig)
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    transform: TransformConfig = Field(default_factory=TransformConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    bitrate: BitrateConfig = Field(default_factory=BitrateConfig)


class ProcessingPresetCreate(BaseModel):
    """创建画面处理预设请求"""
    name: str
    intensity: Literal["light", "standard", "strong", "custom"] = "standard"
    is_default: bool = False
    config: ProcessingConfig = Field(default_factory=ProcessingConfig)


class ProcessingPresetResponse(BaseModel):
    """画面处理预设响应"""
    id: int
    name: str
    intensity: str
    is_default: bool
    config: dict[str, Any]


class EffectPreviewRequest(BaseModel):
    """画面处理预览请求"""
    video_path: str
    preset: ProcessingConfig = Field(default_factory=ProcessingConfig)
    start_time: float = 0
    duration: float = 8
    output_path: Optional[str] = None


class EffectApplyRequest(BaseModel):
    """画面处理执行请求"""
    video_path: str
    preset: ProcessingConfig = Field(default_factory=ProcessingConfig)
    output_path: Optional[str] = None


class FilterGraphRequest(BaseModel):
    """滤镜预览请求"""
    preset: ProcessingConfig = Field(default_factory=ProcessingConfig)


class EffectResult(BaseModel):
    """画面处理结果"""
    message: str
    output_path: str
    filter_graph: str
    task_id: Optional[int] = None


def _preset_to_response(preset: ProcessingPreset) -> ProcessingPresetResponse:
    """将数据库模型转换为响应模型"""
    return ProcessingPresetResponse(
        id=preset.id,
        name=preset.name,
        intensity=preset.intensity,
        is_default=bool(preset.is_default),
        config=json.loads(preset.config_json),
    )


def _default_presets() -> list[ProcessingPresetCreate]:
    """内置画面处理预设"""
    light = ProcessingPresetCreate(
        name="轻度处理",
        intensity="light",
        config=ProcessingConfig(
            adjustments=AdjustmentConfig(
                brightness=RandomRange(random=True, min=0.0, max=0.04),
                contrast=RandomRange(random=True, min=1.0, max=1.08),
                saturation=RandomRange(random=True, min=1.0, max=1.06),
                sharpness=RandomRange(random=True, min=0.3, max=0.7),
                denoise=RandomRange(random=True, min=0.0, max=1.0),
            ),
            transform=TransformConfig(flip_horizontal=False, random_rotate=RandomRange(enabled=False, random=True, min=-0.5, max=0.5)),
        ),
    )
    standard = ProcessingPresetCreate(name="标准处理", intensity="standard", is_default=True)
    strong = ProcessingPresetCreate(
        name="强处理",
        intensity="strong",
        config=ProcessingConfig(
            adjustments=AdjustmentConfig(
                brightness=RandomRange(random=True, min=0.02, max=0.12),
                contrast=RandomRange(random=True, min=1.08, max=1.25),
                saturation=RandomRange(random=True, min=1.08, max=1.22),
                sharpness=RandomRange(random=True, min=1.1, max=1.8),
                denoise=RandomRange(random=True, min=1.5, max=3.0),
            ),
            canvas=CanvasConfig(resolution="1080p", mode="crop"),
            transform=TransformConfig(flip_horizontal=True, random_rotate=RandomRange(random=True, min=-1.5, max=1.5)),
            bitrate=BitrateConfig(fixed_kbps=RandomRange(random=False, value=3500, min=3500, max=3500)),
        ),
    )
    return [light, standard, strong]


@router.get("/presets", response_model=list[ProcessingPresetResponse])
async def get_processing_presets(db: Session = Depends(get_db)):
    """获取画面处理预设列表"""
    presets = db.query(ProcessingPreset).order_by(ProcessingPreset.id.asc()).all()
    if not presets:
        for item in _default_presets():
            db.add(ProcessingPreset(
                name=item.name,
                intensity=item.intensity,
                is_default=item.is_default,
                config_json=item.config.model_dump_json(),
            ))
        db.commit()
        presets = db.query(ProcessingPreset).order_by(ProcessingPreset.id.asc()).all()
    return [_preset_to_response(preset) for preset in presets]


@router.post("/presets", response_model=ProcessingPresetResponse)
async def create_processing_preset(request: ProcessingPresetCreate, db: Session = Depends(get_db)):
    """创建画面处理预设"""
    if request.is_default:
        db.query(ProcessingPreset).update({ProcessingPreset.is_default: False})

    preset = ProcessingPreset(
        name=request.name,
        intensity=request.intensity,
        is_default=request.is_default,
        config_json=request.config.model_dump_json(),
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return _preset_to_response(preset)


@router.delete("/presets/{preset_id}")
async def delete_processing_preset(preset_id: int, db: Session = Depends(get_db)):
    """删除画面处理预设"""
    preset = db.query(ProcessingPreset).filter(ProcessingPreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="画面处理预设不存在")
    db.delete(preset)
    db.commit()
    return {"message": "画面处理预设已删除"}


@router.post("/filter-graph")
async def build_filter_graph(request: FilterGraphRequest):
    """生成 ffmpeg 滤镜字符串，便于前端预览参数"""
    processor = FFmpegProcessor()
    preset = request.preset.model_dump()
    return {"filter_graph": processor.build_effect_filter_graph(preset)}


@router.post("/preview", response_model=EffectResult)
async def preview_effects(request: EffectPreviewRequest):
    """生成画面处理短片段预览"""
    processor = FFmpegProcessor()
    preset = request.preset.model_dump()
    try:
        output_path = processor.apply_effects(
            video_path=request.video_path,
            preset=preset,
            output_path=request.output_path,
            preview=True,
            start_time=request.start_time,
            duration=request.duration,
        )
        return EffectResult(
            message="画面处理预览已生成",
            output_path=output_path,
            filter_graph=processor.build_effect_filter_graph(preset),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/apply", response_model=EffectResult)
async def apply_effects(request: EffectApplyRequest, db: Session = Depends(get_db)):
    """执行完整画面处理任务"""
    processor = FFmpegProcessor()
    preset = request.preset.model_dump()
    task = DownloadTask(
        video_id=0,
        task_type="effects",
        status="processing",
        progress=0,
        params=json.dumps({"video_path": request.video_path, "preset": preset}, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        output_path = processor.apply_effects(
            video_path=request.video_path,
            preset=preset,
            output_path=request.output_path,
            preview=False,
        )
        task.status = "completed"
        task.progress = 100
        task.output_path = output_path
        db.commit()
        return EffectResult(
            message="画面处理完成",
            output_path=output_path,
            filter_graph=processor.build_effect_filter_graph(preset),
            task_id=task.id,
        )
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
