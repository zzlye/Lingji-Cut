# backend/api/subtitles.py
# 字幕 API 路由 - 提供字幕处理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import os

from ..core import Downloader, FFmpegProcessor, SubtitleEngine, TextEngine
from ..core.paths import detect_video_workspace, ensure_project_dirs, ensure_video_workspace
from ..core.process_control import TaskControlRequested
from ..models import DownloadTask, SubtitlePreset, TextProviderProfile, VideoSource, get_db
from ..utils import decrypt_api_key

# 创建路由器
router = APIRouter(prefix="/subtitles", tags=["subtitles"])


class SubtitlePresetCreate(BaseModel):
    """创建字幕预设请求"""
    name: str
    is_default: bool = False
    line_mode: str = "single"
    language: str = "auto"
    font_name: str = "Microsoft YaHei"
    font_size: int = 48
    secondary_font_size: int = 42
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
    secondary_font_size: int
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


class SubtitleEntryPayload(BaseModel):
    """字幕校对条目"""
    index: int = 1
    start: str
    end: str
    text: str


class SubtitleEntriesProcessRequest(BaseModel):
    """按字幕条目执行 AI 润色/翻译/生成"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    profile_id: int
    operation: str = "polish"
    target_language: Optional[str] = None


class SubtitleEntriesProcessResponse(BaseModel):
    """字幕条目 AI 处理响应"""
    message: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    plain_text: str = ""
    operation: str


class SubtitleCorrectionParseFileRequest(BaseModel):
    """读取本地字幕文件请求"""
    subtitle_path: str


class SubtitleCorrectionParseTextRequest(BaseModel):
    """解析粘贴字幕文本请求"""
    content: str
    format: str = "srt"


class SubtitleCorrectionSaveRequest(BaseModel):
    """保存校对后字幕请求"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    output_path: Optional[str] = None
    file_name: Optional[str] = None
    format: str = "srt"
    source_path: Optional[str] = None


class SubtitleCorrectionSaveAssRequest(BaseModel):
    """保存校对后 ASS 字幕请求"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    output_path: Optional[str] = None
    file_name: Optional[str] = None
    preset_id: Optional[int] = None
    source_path: Optional[str] = None


class SubtitleCorrectionResponse(BaseModel):
    """字幕校对响应"""
    message: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    plain_text: str = ""
    output_path: Optional[str] = None
    format: Optional[str] = None


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
        "secondary_font_size": preset.secondary_font_size,
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
    if ext == ".ass":
        return engine.parse_ass(subtitle_path)
    raise HTTPException(status_code=400, detail=f"暂不支持的字幕格式: {ext}")


def _parse_subtitle_text(engine: SubtitleEngine, content: str, subtitle_format: str) -> list[dict]:
    """按格式解析粘贴的字幕文本"""
    normalized_format = subtitle_format.lower().lstrip(".")
    if normalized_format == "srt":
        return engine.parse_srt_content(content)
    if normalized_format == "vtt":
        return engine.parse_vtt_content(content)
    raise HTTPException(status_code=400, detail=f"暂不支持的字幕文本格式: {subtitle_format}")


def _normalize_correction_entries(engine: SubtitleEngine, entries: list[SubtitleEntryPayload]) -> list[dict]:
    """清理前端提交的字幕条目，保证时间码和序号稳定"""
    normalized: list[dict] = []
    for item in entries:
        text = engine.clean_subtitle_text_for_output(item.text)
        if not text or engine.is_meaningless_subtitle_text(text):
            continue
        start = engine.normalize_srt_time(item.start)
        end = engine.normalize_srt_time(item.end)
        normalized.append({
            "index": len(normalized) + 1,
            "start": start,
            "end": end,
            "text": text,
        })
    if not normalized:
        raise HTTPException(status_code=400, detail="字幕条目不能为空")
    return normalized


def _entry_payloads(entries: list[dict]) -> list[SubtitleEntryPayload]:
    """把字幕字典转换成 API 响应模型"""
    return [
        SubtitleEntryPayload(
            index=int(entry.get("index") or index),
            start=str(entry.get("start") or "00:00:00,000"),
            end=str(entry.get("end") or "00:00:00,000"),
            text=str(entry.get("text") or ""),
        )
        for index, entry in enumerate(entries, 1)
    ]


def _safe_subtitle_output_path(
    output_path: Optional[str],
    file_name: Optional[str],
    extension: str,
    source_path: Optional[str] = None,
) -> str:
    """生成字幕输出路径，优先复用当前字幕或视频所属目录，避免不同视频的素材混在一起"""
    if output_path and output_path.strip():
        path = os.path.abspath(os.path.expanduser(output_path.strip()))
    else:
        base_dir = _subtitle_output_base_dir(source_path)
        raw_name = (file_name or _subtitle_default_file_name(source_path, extension)).strip()
        safe_name = _sanitize_subtitle_file_name(raw_name, extension)
        path = os.path.join(base_dir, safe_name)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _subtitle_output_base_dir(source_path: Optional[str]) -> str:
    """根据当前字幕或视频路径推导默认输出目录，优先落到该视频的工作目录 output 下"""
    raw_source_path = str(source_path or "").strip()
    if raw_source_path:
        normalized = os.path.abspath(os.path.expanduser(raw_source_path))
        workspace_paths = detect_video_workspace(normalized)
        if workspace_paths:
            return workspace_paths["output_dir"]
        parent_dir = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
        if parent_dir:
            return parent_dir
    return ensure_project_dirs()["output_dir"]


def _subtitle_default_file_name(source_path: Optional[str], extension: str) -> str:
    """没有显式文件名时，优先沿用源文件名，避免同一视频下生成难以辨认的通用文件名"""
    raw_source_path = str(source_path or "").strip()
    if raw_source_path:
        base_name = os.path.splitext(os.path.basename(raw_source_path))[0].strip()
        if base_name:
            return f"{base_name}.{extension}"
    return f"manual_subtitle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"


def _sanitize_subtitle_file_name(file_name: str, extension: str) -> str:
    """把用户输入的文件名整理成安全路径片段，并补齐目标扩展名"""
    safe_name = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in str(file_name or "").strip())
    safe_name = safe_name.strip("._") or "manual_subtitle"
    if not safe_name.lower().endswith(f".{extension}"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.{extension}"
    return safe_name


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


def _default_subtitle_presets() -> list[SubtitlePresetCreate]:
    """内置字幕样式预设，保证字幕设置页和一键流程开箱即用"""
    return [
        # 默认：白字黑边、底部居中，适配绝大多数横屏短视频
        SubtitlePresetCreate(name="默认字幕", is_default=True, line_mode="single"),
        # 醒目大字：字号更大、描边更粗、上移边距，适合强调或竖屏
        SubtitlePresetCreate(name="醒目大字", line_mode="single", font_size=56, outline_width=3, margin_v=48),
    ]


def ensure_default_subtitle_presets(db: Session) -> None:
    """字幕预设为空时惰性创建内置默认预设。

    一键流程的 _pick_subtitle_preset 直接查库取 is_default 预设，
    因此后端启动时也会调用本函数，避免新用户未进设置页就一键完成时拿不到统一字幕样式。
    """
    if db.query(SubtitlePreset).first():
        return
    for item in _default_subtitle_presets():
        db.add(SubtitlePreset(**item.model_dump()))
    db.commit()


@router.get("/presets", response_model=list[SubtitlePresetResponse])
async def get_presets(db: Session = Depends(get_db)):
    """获取所有字幕预设；表为空时惰性创建内置默认预设，避免新用户面对空列表"""
    ensure_default_subtitle_presets(db)
    return db.query(SubtitlePreset).order_by(SubtitlePreset.id.asc()).all()


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


@router.post("/process-entries", response_model=SubtitleEntriesProcessResponse)
async def process_subtitle_entries(request: SubtitleEntriesProcessRequest, db: Session = Depends(get_db)):
    """按字幕条目执行 AI 处理，并保持原有时间轴"""
    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")

    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)

    try:
        processed_entries = await TextEngine().process_subtitle_entries(
            entries=entries,
            provider_type=profile.provider_type,
            api_key=decrypt_api_key(profile.api_key_encrypted),
            base_url=profile.base_url,
            model=profile.model or "",
            settings=_load_text_settings(profile),
            operation=request.operation,
            target_language=request.target_language or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return SubtitleEntriesProcessResponse(
        message="字幕条目处理完成",
        entries=_entry_payloads(processed_entries),
        plain_text=entries_to_plain_text(processed_entries),
        operation=request.operation,
    )


@router.post("/parse-file", response_model=SubtitleCorrectionResponse)
async def parse_subtitle_file(request: SubtitleCorrectionParseFileRequest):
    """读取本地字幕文件，返回可手动校对的条目"""
    raw_path = request.subtitle_path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="请填写字幕文件路径")
    subtitle_path = os.path.abspath(os.path.expanduser(raw_path))
    if not os.path.exists(subtitle_path):
        raise HTTPException(status_code=404, detail=f"字幕文件不存在: {subtitle_path}")

    engine = SubtitleEngine()
    entries = _parse_subtitle_entries(engine, subtitle_path)
    if not entries:
        raise HTTPException(status_code=400, detail="字幕文件为空或格式无法识别")

    return SubtitleCorrectionResponse(
        message=f"已读取 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=subtitle_path,
        format=os.path.splitext(subtitle_path)[1].lower().lstrip("."),
    )


@router.post("/parse-text", response_model=SubtitleCorrectionResponse)
async def parse_subtitle_text(request: SubtitleCorrectionParseTextRequest):
    """解析粘贴的 SRT/VTT 文本，返回可编辑字幕条目"""
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="字幕文本不能为空")

    engine = SubtitleEngine()
    entries = _parse_subtitle_text(engine, request.content, request.format)
    if not entries:
        raise HTTPException(status_code=400, detail="没有解析到有效字幕条目")

    return SubtitleCorrectionResponse(
        message=f"已解析 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        format=request.format.lower().lstrip("."),
    )


@router.post("/save", response_model=SubtitleCorrectionResponse)
async def save_corrected_subtitle(request: SubtitleCorrectionSaveRequest):
    """保存手动校对后的 SRT 字幕文件"""
    normalized_format = request.format.lower().lstrip(".")
    if normalized_format != "srt":
        raise HTTPException(status_code=400, detail="当前只支持保存 SRT 字幕")

    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)
    output_path = _safe_subtitle_output_path(request.output_path, request.file_name, "srt", request.source_path)
    engine.save_srt(entries, output_path)

    return SubtitleCorrectionResponse(
        message=f"已保存 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=output_path,
        format="srt",
    )


@router.post("/save-ass", response_model=SubtitleCorrectionResponse)
async def save_corrected_ass(request: SubtitleCorrectionSaveAssRequest, db: Session = Depends(get_db)):
    """按当前字幕预设生成 ASS 字幕文件"""
    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)
    preset = _pick_subtitle_preset(db, request.preset_id)
    preset_dict = _preset_to_dict(preset)
    display_entries = engine.normalize_entries_for_display(entries, preset_dict)
    output_path = _safe_subtitle_output_path(request.output_path, request.file_name, "ass", request.source_path)
    engine.generate_ass(display_entries, output_path, preset_dict)

    return SubtitleCorrectionResponse(
        message=f"已生成 ASS 字幕 {len(display_entries)} 条",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=output_path,
        format="ass",
    )


@router.post("/render", response_model=SubtitleRenderResponse)
def render_subtitles(request: SubtitleRenderRequest, db: Session = Depends(get_db)):
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
        paths = ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
        subtitle_path = request.subtitle_path
        if not subtitle_path:
            subtitle_path = Downloader().download_subtitle(
                url=video.url,
                language=language,
                output_dir=paths["output_dir"],
                sub_type=request.sub_type,
                control_keys=[f"task:{task.id}"],
            )
        if not subtitle_path or not os.path.exists(subtitle_path):
            raise FileNotFoundError("字幕文件不存在")

        engine = SubtitleEngine()
        entries = _parse_subtitle_entries(engine, subtitle_path)
        if not entries:
            raise RuntimeError("字幕文件为空，无法生成 ASS")

        base_name = os.path.splitext(os.path.basename(request.video_path))[0]
        ass_path = os.path.join(paths["output_dir"], f"{base_name}_{language}.ass")
        display_entries = engine.normalize_entries_for_display(entries, preset_dict)
        engine.generate_ass(display_entries, ass_path, preset_dict)

        output_path = None
        if request.burn_in:
            def on_burn_progress(progress: float) -> None:
                """同步字幕烧录进度"""
                task.progress = max(0.0, min(99.0, progress))
                db.commit()

            output_path = FFmpegProcessor().burn_subtitles(
                video_path=request.video_path,
                subtitle_path=ass_path,
                output_path=request.output_path,
                preset=preset_dict,
                control_keys=[f"task:{task.id}"],
                progress_callback=on_burn_progress,
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
    except TaskControlRequested as exc:
        task.status = "paused" if exc.action == "pause" else "cancelled"
        task.error_message = "用户暂停，等待继续" if exc.action == "pause" else "用户取消"
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
