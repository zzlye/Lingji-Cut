# backend/api/automation.py
# 自动化 API 路由 - 在后端串联解析、下载、画面处理、字幕、配音和导出

import json
import os
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core import DedupChecker, Downloader, FFmpegProcessor, SubtitleEngine, TextEngine, VoiceEngine
from ..core.paths import ensure_project_dirs
from ..models import AutomationJobRecord, DownloadTask, SessionLocal, SubtitlePreset, TextProviderProfile, VideoSource, VoiceProviderProfile, get_db
from ..utils import decrypt_api_key
from .subtitles import _parse_subtitle_entries, _preset_to_dict, entries_to_plain_text


router = APIRouter(prefix="/automation", tags=["automation"])

# 后台自动化任务线程池，避免多个长视频同时阻塞 API 线程。
AUTOMATION_EXECUTOR = ThreadPoolExecutor(max_workers=2)

# 自动化阶段权重，用于计算总进度。字幕和配音可跳过，所以权重略低。
STAGE_WEIGHTS = {
    "parse": 8,
    "download": 24,
    "effects": 22,
    "subtitle": 18,
    "voice": 10,
    "export": 18,
}


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
    progress: float = 0
    task_id: Optional[int] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None


class AutomationJobResponse(BaseModel):
    """自动化任务状态响应"""
    id: str
    source_url: str
    video_id: Optional[int] = None
    title: Optional[str] = None
    status: str
    progress: float
    current_step: Optional[str] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    stages: list[AutomationStageResult] = Field(default_factory=list)
    subtitle_text: str = ""
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AutomationStartResponse(BaseModel):
    """启动自动化任务响应"""
    message: str
    job_id: str


class AutomationRunResponse(BaseModel):
    """一键自动流程响应"""
    message: str
    video_id: int
    title: Optional[str] = None
    output_path: str
    stages: list[AutomationStageResult]
    subtitle_text: str = ""


def _default_stages() -> list[dict[str, Any]]:
    """生成默认阶段状态"""
    return [
        {"key": "parse", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        {"key": "download", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        {"key": "effects", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        {"key": "subtitle", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        {"key": "voice", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        {"key": "export", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
    ]


def _load_job_stages(job: AutomationJobRecord) -> list[dict[str, Any]]:
    """读取自动化任务阶段 JSON"""
    if not job.stages:
        return _default_stages()
    try:
        data = json.loads(job.stages)
        return data if isinstance(data, list) else _default_stages()
    except json.JSONDecodeError:
        return _default_stages()


def _calculate_job_progress(stages: list[dict[str, Any]]) -> int:
    """根据阶段权重计算自动化任务总进度"""
    total_weight = sum(STAGE_WEIGHTS.values())
    finished = 0.0
    for stage in stages:
        key = str(stage.get("key", ""))
        weight = STAGE_WEIGHTS.get(key, 0)
        status = stage.get("status")
        progress = float(stage.get("progress") or 0)
        if status in {"completed", "skipped"}:
            progress = 100
        finished += weight * max(0, min(100, progress)) / 100
    return round(finished / total_weight * 100) if total_weight else 0


def _save_job_stages(db: Session, job: AutomationJobRecord, stages: list[dict[str, Any]]) -> None:
    """保存阶段状态并同步总体进度"""
    job.stages = json.dumps(stages, ensure_ascii=False)
    job.progress = _calculate_job_progress(stages)
    db.commit()


def _update_job_stage(
    db: Session,
    job: AutomationJobRecord,
    key: str,
    status: str,
    progress: Optional[float] = None,
    task_id: Optional[int] = None,
    output_path: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """更新自动化任务的单个阶段"""
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("key") != key:
            continue
        stage["status"] = status
        stage["progress"] = 100 if progress is None and status in {"completed", "skipped"} else (progress if progress is not None else stage.get("progress", 0))
        if task_id is not None:
            stage["task_id"] = task_id
        if output_path is not None:
            stage["output_path"] = output_path
        stage["error_message"] = error_message
        break
    job.current_step = {
        "parse": "解析视频",
        "download": "下载入库",
        "effects": "画面处理",
        "subtitle": "字幕处理",
        "voice": "配音生成",
        "export": "合成导出",
    }.get(key, job.current_step)
    _save_job_stages(db, job, stages)


def _job_to_response(job: AutomationJobRecord) -> AutomationJobResponse:
    """把数据库自动化任务转换成 API 响应"""
    stages = [
        AutomationStageResult(
            key=str(stage.get("key")),
            status=str(stage.get("status")),
            progress=float(stage.get("progress") or 0),
            task_id=stage.get("task_id"),
            output_path=stage.get("output_path"),
            error_message=stage.get("error_message"),
        )
        for stage in _load_job_stages(job)
    ]
    return AutomationJobResponse(
        id=job.id,
        source_url=job.source_url,
        video_id=job.video_id,
        title=job.title,
        status=job.status,
        progress=job.progress or 0,
        current_step=job.current_step,
        output_path=job.output_path,
        error_message=job.error_message,
        stages=stages,
        subtitle_text=job.subtitle_text or "",
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _stage_by_key(job: Optional[AutomationJobRecord], key: str) -> Optional[dict[str, Any]]:
    """按阶段 key 读取自动化任务阶段记录"""
    if not job:
        return None
    for stage in _load_job_stages(job):
        if stage.get("key") == key:
            return stage
    return None


def _stage_output_if_reusable(job: Optional[AutomationJobRecord], key: str) -> Optional[str]:
    """返回可复用的阶段输出文件路径"""
    stage = _stage_by_key(job, key)
    if not stage or stage.get("status") not in {"completed", "skipped"}:
        return None
    output_path = stage.get("output_path")
    if isinstance(output_path, str) and output_path and os.path.exists(output_path):
        return output_path
    return None


def _mark_stage_reused(db: Session, job: Optional[AutomationJobRecord], key: str, output_path: Optional[str] = None) -> None:
    """把已完成阶段标记为复用，保持前端能看到断点续跑进度"""
    if not job:
        return
    _update_job_stage(db, job, key, "completed", progress=100, output_path=output_path)


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


def _create_task(db: Session, video_id: int, task_type: str, params: Optional[dict[str, Any]] = None, parent_job_id: Optional[str] = None) -> DownloadTask:
    """创建后端自动流程子任务"""
    task = DownloadTask(
        video_id=video_id,
        task_type=task_type,
        status="processing",
        progress=0,
        params=json.dumps(params or {}, ensure_ascii=False),
        parent_job_id=parent_job_id,
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


def map_text_to_timed_entries(text: str, original_entries: list[dict]) -> list[dict[str, str | int]]:
    """把文本 API 返回内容映射回原字幕时间轴"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]
    if not lines:
        return []
    if not original_entries:
        return _plain_text_to_entries(text)

    entries: list[dict[str, str | int]] = []
    source_count = len(original_entries)
    line_count = len(lines)

    if line_count <= source_count:
        for index, line in enumerate(lines, 1):
            start_index = int((index - 1) * source_count / line_count)
            end_index = max(start_index, int(index * source_count / line_count) - 1)
            entries.append({
                "index": index,
                "start": str(original_entries[start_index].get("start", "00:00:00,000")),
                "end": str(original_entries[min(end_index, source_count - 1)].get("end", "00:00:01,000")),
                "text": line,
            })
        return entries

    for index, entry in enumerate(original_entries, 1):
        start_line = int((index - 1) * line_count / source_count)
        end_line = max(start_line + 1, int(index * line_count / source_count))
        entries.append({
            "index": index,
            "start": str(entry.get("start", "00:00:00,000")),
            "end": str(entry.get("end", "00:00:01,000")),
            "text": "\n".join(lines[start_line:end_line]),
        })
    return entries


def _plain_text_to_entries(text: str) -> list[dict[str, str | int]]:
    """没有原时间轴时，把纯文本转换成可渲染字幕条目"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]

    return [
        {
            "index": index,
            "start": _seconds_to_srt_time(index - 1),
            "end": _seconds_to_srt_time(index),
            "text": line,
        }
        for index, line in enumerate(lines, 1)
    ]


def _seconds_to_srt_time(total_seconds: int) -> str:
    """把秒数转换成合法 SRT 时间码"""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},000"


def _run_automation_sync(request: AutomationRunRequest, db: Session, job: Optional[AutomationJobRecord] = None, resume_from_checkpoint: bool = False) -> AutomationRunResponse:
    """同步执行完整一键自动流程，供直接调用和后台任务复用"""
    downloader = Downloader()
    processor = FFmpegProcessor()
    stages: list[AutomationStageResult] = []
    subtitle_text = ""
    subtitle_ass_path: Optional[str] = None
    audio_path: Optional[str] = None

    video = _parse_or_update_video(db, request.url, downloader)
    stages.append(AutomationStageResult(key="parse", status="completed"))
    if job:
        job.video_id = video.id
        job.title = video.title or "一键自动流程"
        job.status = "running"
        _update_job_stage(db, job, "parse", "completed")

    reusable_download_path = _stage_output_if_reusable(job, "download") if resume_from_checkpoint else None
    if reusable_download_path:
        downloaded_path = reusable_download_path
        _mark_stage_reused(db, job, "download", downloaded_path)
        stages.append(AutomationStageResult(key="download", status="completed", progress=100, output_path=downloaded_path))
    else:
        download_task = _create_task(db, video.id, "download", {"format_id": request.format_id}, job.id if job else None)
        if job:
            _update_job_stage(db, job, "download", "running", progress=0, task_id=download_task.id)

        def on_progress(progress: float, _: str) -> None:
            """更新下载任务和自动化任务进度"""
            download_task.progress = progress
            db.commit()
            if job:
                _update_job_stage(db, job, "download", "running", progress=progress, task_id=download_task.id)

        try:
            downloaded_path = downloader.download_video(
                url=video.url,
                format_id=request.format_id,
                output_format="mp4",
                progress_callback=on_progress,
            )
            _complete_task(db, download_task, downloaded_path)
            stages.append(AutomationStageResult(key="download", status="completed", task_id=download_task.id, output_path=downloaded_path))
            if job:
                _update_job_stage(db, job, "download", "completed", task_id=download_task.id, output_path=downloaded_path)
        except Exception as exc:
            _fail_task(db, download_task, exc)
            if job:
                _update_job_stage(db, job, "download", "failed", task_id=download_task.id, error_message=str(exc))
            raise

    reusable_effects_path = _stage_output_if_reusable(job, "effects") if resume_from_checkpoint else None
    if reusable_effects_path:
        effects_path = reusable_effects_path
        _mark_stage_reused(db, job, "effects", effects_path)
        stages.append(AutomationStageResult(key="effects", status="completed", progress=100, output_path=effects_path))
    else:
        effects_task = _create_task(db, video.id, "effects", {"preset": request.processing_preset}, job.id if job else None)
        if job:
            _update_job_stage(db, job, "effects", "running", progress=15, task_id=effects_task.id)
        try:
            effects_path = processor.apply_effects(
                video_path=downloaded_path,
                preset=request.processing_preset,
            )
            _complete_task(db, effects_task, effects_path)
            stages.append(AutomationStageResult(key="effects", status="completed", task_id=effects_task.id, output_path=effects_path))
            if job:
                _update_job_stage(db, job, "effects", "completed", task_id=effects_task.id, output_path=effects_path)
        except Exception as exc:
            _fail_task(db, effects_task, exc)
            if job:
                _update_job_stage(db, job, "effects", "failed", task_id=effects_task.id, error_message=str(exc))
            raise

    video_for_export = effects_path
    reusable_subtitle_path = _stage_output_if_reusable(job, "subtitle") if resume_from_checkpoint else None
    if reusable_subtitle_path:
        video_for_export = reusable_subtitle_path if request.burn_subtitles else video_for_export
        subtitle_ass_path = reusable_subtitle_path if reusable_subtitle_path.lower().endswith(".ass") else None
        subtitle_text = job.subtitle_text or ""
        _mark_stage_reused(db, job, "subtitle", reusable_subtitle_path)
        stages.append(AutomationStageResult(key="subtitle", status="completed", progress=100, output_path=reusable_subtitle_path))
    else:
        subtitle_task = _create_task(db, video.id, "subtitle", parent_job_id=job.id if job else None)
        if job:
            _update_job_stage(db, job, "subtitle", "running", progress=10, task_id=subtitle_task.id)
        try:
            preset = _pick_subtitle_preset(db, request.subtitle_preset_id)
            track = _pick_subtitle_track(video, request.subtitle_language or (preset.language if preset else None))
            if not track and not preset:
                subtitle_task.status = "completed"
                subtitle_task.progress = 100
                subtitle_task.error_message = "没有检测到字幕轨，已跳过字幕"
                db.commit()
                stages.append(AutomationStageResult(key="subtitle", status="skipped", task_id=subtitle_task.id, error_message=subtitle_task.error_message))
                if job:
                    _update_job_stage(db, job, "subtitle", "skipped", task_id=subtitle_task.id, error_message=subtitle_task.error_message)
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
                if job:
                    _update_job_stage(db, job, "subtitle", "running", progress=35, task_id=subtitle_task.id)
                engine = SubtitleEngine()
                entries = _parse_subtitle_entries(engine, subtitle_path)
                if not entries:
                    raise RuntimeError("字幕文件为空，无法继续自动字幕处理")
                subtitle_text = entries_to_plain_text(entries)
                text_profile = _pick_text_profile(db, request.text_profile_id)
                if text_profile and request.subtitle_operation != "none":
                    try:
                        processed_text = asyncio.run(TextEngine().process_text(
                            text=subtitle_text,
                            provider_type=text_profile.provider_type,
                            api_key=decrypt_api_key(text_profile.api_key_encrypted),
                            base_url=text_profile.base_url,
                            model=text_profile.model or "",
                            settings=_load_profile_settings(text_profile),
                            operation=request.subtitle_operation,
                            target_language=request.subtitle_target_language or "",
                        ))
                        processed_entries = map_text_to_timed_entries(processed_text, entries)
                        if processed_entries:
                            entries = processed_entries
                            subtitle_text = processed_text
                    except Exception:
                        # 文本 API 是增强能力，失败时继续使用原始 YouTube 字幕完成主流程。
                        pass
                if job:
                    job.subtitle_text = subtitle_text
                    _update_job_stage(db, job, "subtitle", "running", progress=70, task_id=subtitle_task.id)
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
                if job:
                    _update_job_stage(db, job, "subtitle", "completed", task_id=subtitle_task.id, output_path=subtitle_task.output_path)
        except Exception as exc:
            _fail_task(db, subtitle_task, exc)
            stages.append(AutomationStageResult(key="subtitle", status="skipped", task_id=subtitle_task.id, error_message=str(exc)))
            if job:
                _update_job_stage(db, job, "subtitle", "skipped", task_id=subtitle_task.id, error_message=str(exc))

    voice_profile = _pick_voice_profile(db, request.voice_profile_id) if request.enable_voice else None
    reusable_audio_path = _stage_output_if_reusable(job, "voice") if resume_from_checkpoint else None
    if reusable_audio_path:
        audio_path = reusable_audio_path
        _mark_stage_reused(db, job, "voice", audio_path)
        stages.append(AutomationStageResult(key="voice", status="completed", progress=100, output_path=audio_path))
    elif voice_profile:
        voice_task = _create_task(db, video.id, "voice", parent_job_id=job.id if job else None)
        if job:
            _update_job_stage(db, job, "voice", "running", progress=15, task_id=voice_task.id)
        try:
            settings = _load_profile_settings(voice_profile)
            voice = settings.get("voice") or voice_profile.voice or "alloy"
            voice_text = (request.voice_text or subtitle_text or _fallback_voice_text(video)).strip()
            output_ext = str(settings.get("format") or "mp3").lower()
            audio_path = os.path.join(ensure_project_dirs()["output_dir"], f"{video.video_id}_voice.{output_ext}")
            audio_path = asyncio.run(VoiceEngine().generate_voice(
                text=voice_text,
                output_path=audio_path,
                provider_type=voice_profile.provider_type,
                voice=voice,
                api_key=decrypt_api_key(voice_profile.api_key_encrypted),
                base_url=voice_profile.base_url,
                model=voice_profile.voice or "",
                settings=settings,
            ))
            _complete_task(db, voice_task, audio_path)
            stages.append(AutomationStageResult(key="voice", status="completed", task_id=voice_task.id, output_path=audio_path))
            if job:
                _update_job_stage(db, job, "voice", "completed", task_id=voice_task.id, output_path=audio_path)
        except Exception as exc:
            _fail_task(db, voice_task, exc)
            audio_path = None
            stages.append(AutomationStageResult(key="voice", status="skipped", task_id=voice_task.id, error_message=str(exc)))
            if job:
                _update_job_stage(db, job, "voice", "skipped", task_id=voice_task.id, error_message=str(exc))
    else:
        stages.append(AutomationStageResult(key="voice", status="skipped", error_message="没有启用或没有已保存配音配置"))
        if job:
            _update_job_stage(db, job, "voice", "skipped", error_message="没有启用或没有已保存配音配置")

    export_task = _create_task(db, video.id, "export", {"output_format": request.output_format}, job.id if job else None)
    if job:
        _update_job_stage(db, job, "export", "running", progress=15, task_id=export_task.id)
    try:
        working_video = video_for_export
        if subtitle_ass_path and not request.burn_subtitles:
            working_video = processor.burn_subtitles(
                video_path=working_video,
                subtitle_path=subtitle_ass_path,
            )
            export_task.progress = 35
            db.commit()
            if job:
                _update_job_stage(db, job, "export", "running", progress=35, task_id=export_task.id)
        if audio_path:
            working_video = processor.merge_audio_video(
                video_path=working_video,
                audio_path=audio_path,
                mode=request.audio_mode,
                volume_ratio=request.original_volume,
            )
            export_task.progress = 70
            db.commit()
            if job:
                _update_job_stage(db, job, "export", "running", progress=70, task_id=export_task.id)
        output_path = processor.convert_format(
            input_path=working_video,
            output_format=request.output_format,
        )
        _complete_task(db, export_task, output_path)
        stages.append(AutomationStageResult(key="export", status="completed", task_id=export_task.id, output_path=output_path))
        if job:
            job.output_path = output_path
            job.status = "completed"
            job.current_step = "流程完成"
            job.completed_at = datetime.now()
            _update_job_stage(db, job, "export", "completed", task_id=export_task.id, output_path=output_path)
            job.progress = 100
            db.commit()
    except Exception as exc:
        _fail_task(db, export_task, exc)
        if job:
            _update_job_stage(db, job, "export", "failed", task_id=export_task.id, error_message=str(exc))
        raise

    return AutomationRunResponse(
        message="一键自动流程完成",
        video_id=video.id,
        title=video.title,
        output_path=output_path,
        stages=stages,
        subtitle_text=subtitle_text,
    )


def _run_background_job(job_id: str, resume_from_checkpoint: bool = False) -> None:
    """后台线程入口：读取任务参数并执行一键流程"""
    db = SessionLocal()
    try:
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
        if not job:
            return
        job.status = "running"
        job.current_step = "准备自动处理"
        db.commit()

        params = json.loads(job.params or "{}")
        request = AutomationRunRequest(**params)
        _run_automation_sync(request, db, job, resume_from_checkpoint=resume_from_checkpoint)
    except Exception as exc:
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(exc)
            job.current_step = "流程失败"
            job.completed_at = datetime.now()
            db.commit()
    finally:
        db.close()


def _reset_job_for_retry(job: AutomationJobRecord) -> None:
    """重置失败任务，保留原始参数后重新进入后台队列"""
    job.status = "pending"
    job.progress = 0
    job.current_step = "等待重试"
    job.output_path = None
    job.subtitle_text = None
    job.error_message = None
    job.completed_at = None
    job.stages = json.dumps(_default_stages(), ensure_ascii=False)


def _prepare_job_for_resume(job: AutomationJobRecord) -> None:
    """准备断点续跑，保留已完成阶段并清理失败阶段"""
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("status") in {"running", "failed"}:
            stage["status"] = "pending"
            stage["progress"] = 0
            stage["task_id"] = None
            stage["output_path"] = None
            stage["error_message"] = None

    job.status = "pending"
    job.progress = _calculate_job_progress(stages)
    job.current_step = "等待继续"
    job.output_path = None
    job.error_message = None
    job.completed_at = None
    job.stages = json.dumps(stages, ensure_ascii=False)


@router.post("/run", response_model=AutomationRunResponse)
def run_automation(request: AutomationRunRequest, db: Session = Depends(get_db)):
    """执行完整一键自动流程"""
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="请填写 YouTube 链接")

    try:
        return _run_automation_sync(request, db)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/start", response_model=AutomationStartResponse)
def start_automation(request: AutomationRunRequest, db: Session = Depends(get_db)):
    """启动后台一键自动流程，立即返回任务 ID"""
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="请填写 YouTube 链接")

    job_id = f"auto-{uuid.uuid4().hex[:16]}"
    job = AutomationJobRecord(
        id=job_id,
        source_url=request.url,
        title="一键自动流程",
        status="pending",
        progress=0,
        current_step="等待开始",
        stages=json.dumps(_default_stages(), ensure_ascii=False),
        params=request.model_dump_json(),
    )
    db.add(job)
    db.commit()
    AUTOMATION_EXECUTOR.submit(_run_background_job, job_id)
    return AutomationStartResponse(message="自动化任务已启动", job_id=job_id)


@router.get("/jobs", response_model=list[AutomationJobResponse])
def list_automation_jobs(db: Session = Depends(get_db)):
    """获取自动化任务列表"""
    jobs = db.query(AutomationJobRecord).order_by(AutomationJobRecord.created_at.desc()).limit(50).all()
    return [_job_to_response(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=AutomationJobResponse)
def get_automation_job(job_id: str, db: Session = Depends(get_db)):
    """获取自动化任务进度"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return _job_to_response(job)


@router.post("/jobs/{job_id}/retry", response_model=AutomationStartResponse)
def retry_automation_job(job_id: str, db: Session = Depends(get_db)):
    """重试失败的一键自动化任务"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status not in {"failed", "completed"}:
        raise HTTPException(status_code=400, detail="只有失败或已完成的自动化任务可以重试")
    if not job.params:
        raise HTTPException(status_code=400, detail="任务缺少原始参数，无法重试")

    _reset_job_for_retry(job)
    db.commit()
    AUTOMATION_EXECUTOR.submit(_run_background_job, job_id)
    return AutomationStartResponse(message="自动化任务已重新进入队列", job_id=job_id)


@router.post("/jobs/{job_id}/resume", response_model=AutomationStartResponse)
def resume_automation_job(job_id: str, db: Session = Depends(get_db)):
    """从已完成阶段后继续执行一键自动化任务"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status not in {"failed", "completed"}:
        raise HTTPException(status_code=400, detail="只有失败或已完成的自动化任务可以继续处理")
    if not job.params:
        raise HTTPException(status_code=400, detail="任务缺少原始参数，无法继续处理")

    _prepare_job_for_resume(job)
    db.commit()
    AUTOMATION_EXECUTOR.submit(_run_background_job, job_id, True)
    return AutomationStartResponse(message="自动化任务已从断点继续", job_id=job_id)


@router.get("/jobs/{job_id}/events")
async def stream_automation_job_events(job_id: str, request: Request):
    """通过 SSE 持续推送自动化任务进度"""

    async def event_stream():
        last_payload = ""
        while True:
            if await request.is_disconnected():
                break

            db = SessionLocal()
            try:
                job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
                if not job:
                    payload = json.dumps({"error": "自动化任务不存在"}, ensure_ascii=False)
                    yield f"event: error\ndata: {payload}\n\n"
                    break

                payload = _job_to_response(job).model_dump_json()
                if payload != last_payload:
                    yield f"event: job\ndata: {payload}\n\n"
                    last_payload = payload
                if job.status in {"completed", "failed"}:
                    break
            finally:
                db.close()

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
