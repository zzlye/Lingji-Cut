# backend/api/automation.py
# 自动化 API 路由 - 在后端串联解析、下载、画面处理、字幕、配音和导出

import json
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core import DedupChecker, Downloader, FFmpegProcessor, SubtitleEngine, TextEngine, VoiceEngine
from ..core.paths import ensure_project_dirs
from ..models import DownloadTask, SubtitlePreset, TextProviderProfile, VideoSource, VoiceProviderProfile, get_db
from ..utils import decrypt_api_key
from .subtitles import _parse_subtitle_entries, _preset_to_dict, entries_to_plain_text


router = APIRouter(prefix="/automation", tags=["automation"])


class AutomationRunRequest(BaseModel):
    """一键自动流程请求"""
    url: str
    processing_preset: dict[str, Any] = Field(default_factory=dict)
    format_id: Optional[str] = None
    output_format: str = "mp4"
    subtitle_preset_id: Optional[int] = None
    subtitle_language: Optional[str] = None
    text_profile_id: Optional[int] = None
    subtitle_operation: str = "none"
    subtitle_target_language: Optional[str] = None
    burn_subtitles: bool = True
    enable_voice: bool = True
    voice_profile_id: Optional[int] = None
    voice_text: Optional[str] = None
    audio_mode: str = "mix"
    original_volume: float = 0.25


class AutomationStageResult(BaseModel):
    """自动流程阶段结果"""
    key: str
    status: str
    task_id: Optional[int] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None


class AutomationRunResponse(BaseModel):
    """一键自动流程响应"""
    message: str
    video_id: int
    title: Optional[str] = None
    output_path: str
    stages: list[AutomationStageResult]
    subtitle_text: str = ""


def _complete_task(db: Session, task: DownloadTask, output_path: Optional[str] = None) -> None:
    """标记任务完成"""
    task.status = "completed"
    task.progress = 100
    task.output_path = output_path
    task.completed_at = datetime.now()
    db.commit()


def _fail_task(db: Session, task: DownloadTask, error: Exception) -> None:
    """标记任务失败并保存错误"""
    task.status = "failed"
    task.error_message = str(error)
    db.commit()


def _create_task(db: Session, video_id: int, task_type: str, params: Optional[dict[str, Any]] = None) -> DownloadTask:
    """创建后端自动流程子任务"""
    task = DownloadTask(
        video_id=video_id,
        task_type=task_type,
        status="processing",
        progress=0,
        params=json.dumps(params or {}, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _parse_or_update_video(db: Session, url: str, downloader: Downloader) -> VideoSource:
    """解析视频并写入或更新入库记录"""
    video_info = downloader.parse_video(url)
    dedup = DedupChecker(db)
    existing = dedup.check_by_video_id(video_info["platform"], video_info["video_id"])
    if existing:
        existing.url = url
        existing.title = video_info.get("title")
        existing.author = video_info.get("author")
        existing.duration = video_info.get("duration")
        existing.thumbnail_url = video_info.get("thumbnail_url")
        existing.formats = json.dumps(video_info.get("formats", []), ensure_ascii=False)
        existing.subtitles = json.dumps(video_info.get("subtitles", []), ensure_ascii=False)
        db.commit()
        db.refresh(existing)
        return existing
    return dedup.add_video_source(video_info)


def _load_subtitle_tracks(video: VideoSource) -> list[dict[str, Any]]:
    """读取解析阶段保存的字幕轨列表"""
    if not video.subtitles:
        return []
    try:
        data = json.loads(video.subtitles)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _pick_subtitle_track(video: VideoSource, requested_language: Optional[str]) -> Optional[dict[str, Any]]:
    """按用户语言、常用语言和首条字幕轨选择字幕"""
    tracks = _load_subtitle_tracks(video)
    if not tracks:
        return None

    languages = [requested_language, "zh-CN", "zh-Hans", "zh", "en"]
    for language in [item for item in languages if item]:
        for track in tracks:
            if track.get("language") == language:
                return track
    return tracks[0]


def _pick_subtitle_preset(db: Session, preset_id: Optional[int]) -> Optional[SubtitlePreset]:
    """选择指定、默认或首个字幕预设"""
    if preset_id:
        preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
        if not preset:
            raise HTTPException(status_code=404, detail="字幕预设不存在")
        return preset
    return db.query(SubtitlePreset).filter(SubtitlePreset.is_default == True).first() or db.query(SubtitlePreset).first()


def _pick_voice_profile(db: Session, profile_id: Optional[int]) -> Optional[VoiceProviderProfile]:
    """选择指定或首个配音配置"""
    if profile_id:
        profile = db.query(VoiceProviderProfile).filter(VoiceProviderProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="配音配置不存在")
        return profile
    return db.query(VoiceProviderProfile).order_by(VoiceProviderProfile.id.asc()).first()


def _pick_text_profile(db: Session, profile_id: Optional[int]) -> Optional[TextProviderProfile]:
    """选择指定文本配置"""
    if not profile_id:
        return None
    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")
    return profile


def _load_profile_settings(profile: VoiceProviderProfile | TextProviderProfile) -> dict[str, Any]:
    """读取 API 配置里的高级参数"""
    if not profile.extra_params:
        return {}
    try:
        data = json.loads(profile.extra_params)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fallback_voice_text(video: VideoSource) -> str:
    """没有字幕正文时生成最低限度的配音文案"""
    title = (video.title or "").strip()
    if title:
        return f"本期视频内容：{title}"
    return "这是一段自动生成的短视频配音。"


def _plain_text_to_entries(text: str) -> list[dict[str, str | int]]:
    """把文本 API 返回的纯文本转换成可渲染字幕条目"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]

    entries: list[dict[str, str | int]] = []
    for index, line in enumerate(lines, 1):
        start = index - 1
        end = index
        entries.append({
            "index": index,
            "start": _seconds_to_srt_time(start),
            "end": _seconds_to_srt_time(end),
            "text": line,
        })
    return entries


def _seconds_to_srt_time(total_seconds: int) -> str:
    """把秒数转换成合法 SRT 时间码"""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},000"


@router.post("/run", response_model=AutomationRunResponse)
async def run_automation(request: AutomationRunRequest, db: Session = Depends(get_db)):
    """执行完整一键自动流程"""
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="请填写 YouTube 链接")

    downloader = Downloader()
    processor = FFmpegProcessor()
    stages: list[AutomationStageResult] = []
    subtitle_text = ""
    subtitle_ass_path: Optional[str] = None
    audio_path: Optional[str] = None

    try:
        video = _parse_or_update_video(db, request.url, downloader)
        stages.append(AutomationStageResult(key="parse", status="completed"))

        download_task = _create_task(db, video.id, "download", {"format_id": request.format_id})

        def on_progress(progress: float, _: str) -> None:
            """更新自动下载进度"""
            download_task.progress = progress
            db.commit()

        try:
            downloaded_path = downloader.download_video(
                url=video.url,
                format_id=request.format_id,
                output_format="mp4",
                progress_callback=on_progress,
            )
            _complete_task(db, download_task, downloaded_path)
            stages.append(AutomationStageResult(key="download", status="completed", task_id=download_task.id, output_path=downloaded_path))
        except Exception as exc:
            _fail_task(db, download_task, exc)
            raise

        effects_task = _create_task(db, video.id, "effects", {"preset": request.processing_preset})
        try:
            effects_path = processor.apply_effects(
                video_path=downloaded_path,
                preset=request.processing_preset,
            )
            _complete_task(db, effects_task, effects_path)
            stages.append(AutomationStageResult(key="effects", status="completed", task_id=effects_task.id, output_path=effects_path))
        except Exception as exc:
            _fail_task(db, effects_task, exc)
            raise

        video_for_export = effects_path
        subtitle_task = _create_task(db, video.id, "subtitle")
        try:
            preset = _pick_subtitle_preset(db, request.subtitle_preset_id)
            track = _pick_subtitle_track(video, request.subtitle_language or (preset.language if preset else None))
            if not track and not preset:
                subtitle_task.status = "completed"
                subtitle_task.progress = 100
                subtitle_task.error_message = "没有检测到字幕轨，已跳过字幕"
                db.commit()
                stages.append(AutomationStageResult(key="subtitle", status="skipped", task_id=subtitle_task.id, error_message=subtitle_task.error_message))
            else:
                paths = ensure_project_dirs()
                preset_dict = _preset_to_dict(preset)
                language = request.subtitle_language or (track or {}).get("language") or preset_dict.get("language") or "en"
                if language == "auto":
                    language = "en"
                subtitle_path = downloader.download_subtitle(
                    url=video.url,
                    language=language,
                    output_dir=paths["output_dir"],
                    sub_type=(track or {}).get("type", "auto"),
                )
                engine = SubtitleEngine()
                entries = _parse_subtitle_entries(engine, subtitle_path)
                if not entries:
                    raise RuntimeError("字幕文件为空，无法继续自动字幕处理")
                subtitle_text = entries_to_plain_text(entries)
                text_profile = _pick_text_profile(db, request.text_profile_id)
                if text_profile and request.subtitle_operation != "none":
                    try:
                        processed_text = await TextEngine().process_text(
                            text=subtitle_text,
                            provider_type=text_profile.provider_type,
                            api_key=decrypt_api_key(text_profile.api_key_encrypted),
                            base_url=text_profile.base_url,
                            model=text_profile.model or "",
                            settings=_load_profile_settings(text_profile),
                            operation=request.subtitle_operation,
                            target_language=request.subtitle_target_language or "",
                        )
                        processed_entries = _plain_text_to_entries(processed_text)
                        if processed_entries:
                            entries = processed_entries
                            subtitle_text = processed_text
                    except Exception:
                        # 文本 API 是增强能力，失败时继续使用原始 YouTube 字幕完成主流程。
                        pass
                base_name = os.path.splitext(os.path.basename(effects_path))[0]
                subtitle_ass_path = os.path.join(paths["output_dir"], f"{base_name}_{language}.ass")
                engine.generate_ass(entries, subtitle_ass_path, preset_dict)
                if request.burn_subtitles:
                    video_for_export = processor.burn_subtitles(
                        video_path=effects_path,
                        subtitle_path=subtitle_ass_path,
                        preset=preset_dict,
                    )
                _complete_task(db, subtitle_task, video_for_export if request.burn_subtitles else subtitle_ass_path)
                stages.append(AutomationStageResult(key="subtitle", status="completed", task_id=subtitle_task.id, output_path=subtitle_task.output_path))
        except Exception as exc:
            _fail_task(db, subtitle_task, exc)
            stages.append(AutomationStageResult(key="subtitle", status="skipped", task_id=subtitle_task.id, error_message=str(exc)))

        voice_profile = _pick_voice_profile(db, request.voice_profile_id) if request.enable_voice else None
        if voice_profile:
            voice_task = _create_task(db, video.id, "voice")
            try:
                settings = _load_profile_settings(voice_profile)
                voice = settings.get("voice") or voice_profile.voice or "alloy"
                voice_text = (request.voice_text or subtitle_text or _fallback_voice_text(video)).strip()
                output_ext = str(settings.get("format") or "mp3").lower()
                audio_path = os.path.join(ensure_project_dirs()["output_dir"], f"{video.video_id}_voice.{output_ext}")
                audio_path = await VoiceEngine().generate_voice(
                    text=voice_text,
                    output_path=audio_path,
                    provider_type=voice_profile.provider_type,
                    voice=voice,
                    api_key=decrypt_api_key(voice_profile.api_key_encrypted),
                    base_url=voice_profile.base_url,
                    model=voice_profile.voice or "",
                    settings=settings,
                )
                _complete_task(db, voice_task, audio_path)
                stages.append(AutomationStageResult(key="voice", status="completed", task_id=voice_task.id, output_path=audio_path))
            except Exception as exc:
                _fail_task(db, voice_task, exc)
                audio_path = None
                stages.append(AutomationStageResult(key="voice", status="skipped", task_id=voice_task.id, error_message=str(exc)))
        else:
            stages.append(AutomationStageResult(key="voice", status="skipped", error_message="没有启用或没有已保存配音配置"))

        export_task = _create_task(db, video.id, "export", {"output_format": request.output_format})
        try:
            working_video = video_for_export
            if subtitle_ass_path and not request.burn_subtitles:
                working_video = processor.burn_subtitles(
                    video_path=working_video,
                    subtitle_path=subtitle_ass_path,
                )
                export_task.progress = 35
                db.commit()
            if audio_path:
                working_video = processor.merge_audio_video(
                    video_path=working_video,
                    audio_path=audio_path,
                    mode=request.audio_mode,
                    volume_ratio=request.original_volume,
                )
                export_task.progress = 70
                db.commit()
            output_path = processor.convert_format(
                input_path=working_video,
                output_format=request.output_format,
            )
            _complete_task(db, export_task, output_path)
            stages.append(AutomationStageResult(key="export", status="completed", task_id=export_task.id, output_path=output_path))
        except Exception as exc:
            _fail_task(db, export_task, exc)
            raise

        return AutomationRunResponse(
            message="一键自动流程完成",
            video_id=video.id,
            title=video.title,
            output_path=output_path,
            stages=stages,
            subtitle_text=subtitle_text,
        )
    except HTTPException:
        raise
    except Exception as exc:
        stages.append(AutomationStageResult(key="pipeline", status="failed", error_message=str(exc)))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
