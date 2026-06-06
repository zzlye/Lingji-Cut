# backend/api/automation.py
# 自动化 API 路由 - 在后端串联解析、下载、画面处理、字幕、配音和导出

import json
import os
import asyncio
import mimetypes
import re
import time
import uuid
from glob import glob
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock, Semaphore
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..core import DedupChecker, Downloader, FFmpegProcessor, SubtitleEngine, LocalSpeechRecognizer, TextEngine, VoiceEngine
from ..core.paths import ensure_project_dirs, ensure_video_workspace, detect_video_workspace
from ..core.process_control import TaskControlRequested, clear_control_request, raise_if_control_requested
from ..core.task_runtime import clear_job_control_requests, mark_job_child_tasks_controlled, request_job_control, request_stage_task_control
from ..core.tooling import assert_required_tools_available
from ..models import AutomationJobRecord, DownloadTask, SessionLocal, SubtitlePreset, TextProviderProfile, VideoSource, VoiceProviderProfile, get_db
from ..utils import decrypt_api_key
from .subtitles import _parse_subtitle_entries, _preset_to_dict, entries_to_plain_text


router = APIRouter(prefix="/automation", tags=["automation"])

# 后台自动化任务线程池，避免多个长视频同时阻塞 API 线程。
AUTOMATION_EXECUTOR = ThreadPoolExecutor(max_workers=8)

# 批次级并发控制，确保同一批任务按用户设置的并发数执行。
BATCH_SEMAPHORES: dict[str, Semaphore] = {}
BATCH_PAUSED: set[str] = set()
BATCH_SEMAPHORE_LOCK = Lock()
SCHEDULED_AUTOMATION_JOBS: set[str] = set()
SCHEDULED_JOB_LOCK = Lock()
CANCELLED_STATUS = "cancelled"
TERMINAL_STATUSES = {"completed", "failed", CANCELLED_STATUS}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".flac"}
EDITABLE_SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass"}

# 自动化阶段权重，用于计算总进度。字幕和配音可跳过，所以权重略低。
STAGE_WEIGHTS = {
    "parse": 8,
    "download": 24,
    "effects": 22,
    "subtitle": 18,
    "voice": 10,
    "export": 18,
}

# 字幕降级只尝试高价值语言，避免 YouTube 自动翻译列表过长导致任务长时间卡住。
SUBTITLE_FALLBACK_LANGUAGES = ("zh-CN", "zh-Hans", "zh", "en", "ja", "ko")
SUBTITLE_MAX_DOWNLOAD_CANDIDATES = 12


class BannedWordsDetected(RuntimeError):
    """禁词命中异常，用于区分普通字幕失败和策略拦截"""


def _job_control_key(job_id: str) -> str:
    """生成自动化任务控制 key"""
    return f"job:{job_id}"


def _task_control_key(task_id: int) -> str:
    """生成底层任务控制 key"""
    return f"task:{task_id}"


def _control_keys(job: Optional[AutomationJobRecord] = None, task: Optional[DownloadTask] = None) -> list[str]:
    """生成当前阶段使用的控制 key 列表"""
    keys: list[str] = []
    if job:
        keys.append(_job_control_key(job.id))
    if task and task.id:
        keys.append(_task_control_key(task.id))
    return keys


def _check_control(db: Session, job: Optional[AutomationJobRecord] = None, task: Optional[DownloadTask] = None) -> None:
    """阶段边界检查暂停/取消请求"""
    if job:
        db.refresh(job)
        if job.status == "paused":
            raise TaskControlRequested("pause")
        if job.status == CANCELLED_STATUS:
            raise TaskControlRequested("cancel")
    raise_if_control_requested(_control_keys(job, task))


class AutomationRunRequest(BaseModel):
    """一键自动流程请求"""
    url: str
    enable_effects: bool = True
    processing_preset: dict[str, Any] = Field(default_factory=dict)
    format_id: Optional[str] = None
    output_format: str = "mp4"
    export_with_settings: bool = True
    export_settings: dict[str, Any] = Field(default_factory=dict)
    subtitle_preset_id: Optional[int] = None
    subtitle_language: Optional[str] = None
    text_profile_id: Optional[int] = None
    subtitle_operation: str = "none"
    subtitle_target_language: Optional[str] = None
    burn_subtitles: bool = True
    enable_voice: bool = False
    voice_profile_id: Optional[int] = None
    voice_text: Optional[str] = None
    voice_mode: str = "segmented"
    audio_mode: str = "mix"
    original_volume: float = 0.25
    multi_speaker_enabled: bool = False
    speaker_voice_map: dict[str, str] = Field(default_factory=dict)
    glossary_terms: list[dict[str, Any]] = Field(default_factory=list)
    banned_words: list[str] = Field(default_factory=list)
    banned_word_action: str = "warn"


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
    batch_id: Optional[str] = None
    can_pause: bool = False
    can_cancel: bool = False
    can_resume: bool = False
    can_retry: bool = False
    subtitle_asset_path: Optional[str] = None
    source_video_path: Optional[str] = None
    voice_asset_path: Optional[str] = None
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


class AutomationReExportRequest(BaseModel):
    """字幕调整页重新合成导出请求"""
    subtitle_path: Optional[str] = None
    audio_path: Optional[str] = None
    video_path: Optional[str] = None
    output_format: Optional[str] = None
    export_with_settings: Optional[bool] = None
    export_settings: dict[str, Any] = Field(default_factory=dict)
    audio_mode: Optional[str] = None
    original_volume: Optional[float] = None


class AutomationReExportResponse(BaseModel):
    """字幕调整页重新合成导出响应"""
    message: str
    job_id: str
    task_id: int
    output_path: str
    subtitle_path: Optional[str] = None
    audio_path: Optional[str] = None
    video_path: str


class AutomationBatchStartRequest(BaseModel):
    """批量一键自动流程请求"""
    urls: list[str]
    template: AutomationRunRequest
    concurrency: int = Field(default=2, ge=1, le=8)


class AutomationBatchStartResponse(BaseModel):
    """批量启动自动化任务响应"""
    message: str
    batch_id: str
    job_ids: list[str]
    accepted_count: int
    skipped_count: int


class AutomationBatchControlResponse(BaseModel):
    """批量任务控制响应"""
    message: str
    batch_id: str
    affected_count: int


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


def _job_to_response(job: AutomationJobRecord, db: Optional[Session] = None) -> AutomationJobResponse:
    """把数据库自动化任务转换成 API 响应"""
    stages: list[AutomationStageResult] = []
    for stage in _load_job_stages(job):
        status = str(stage.get("status"))
        error_message = stage.get("error_message")
        if job.status == CANCELLED_STATUS and status in {"pending", "running", "paused"}:
            status = CANCELLED_STATUS
            error_message = error_message or "任务已取消"
        stages.append(AutomationStageResult(
            key=str(stage.get("key")),
            status=status,
            progress=float(stage.get("progress") or 0),
            task_id=stage.get("task_id"),
            output_path=stage.get("output_path"),
            error_message=error_message,
        ))
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
        batch_id=_get_batch_id_from_job(job),
        can_pause=job.status in {"pending", "running"},
        can_cancel=job.status in {"pending", "running", "paused"},
        can_resume=job.status in {"paused", "failed", CANCELLED_STATUS, "completed"},
        can_retry=job.status in {"failed", CANCELLED_STATUS, "completed"},
        subtitle_asset_path=_find_job_editable_subtitle_path(job, db),
        source_video_path=_find_job_source_video_path(job),
        voice_asset_path=_find_job_voice_asset_path(job),
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


def _existing_file(path: Optional[str], allowed_exts: Optional[set[str]] = None) -> Optional[str]:
    """返回真实存在且后缀符合预期的绝对路径"""
    if not path or not isinstance(path, str):
        return None
    normalized = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.exists(normalized) or not os.path.isfile(normalized):
        return None
    if allowed_exts and os.path.splitext(normalized)[1].lower() not in allowed_exts:
        return None
    return normalized


def _read_task_params(task: Optional[DownloadTask]) -> dict[str, Any]:
    """安全读取底层任务参数 JSON"""
    if not task or not task.params:
        return {}
    try:
        data = json.loads(task.params)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _subtitle_task_record(job: Optional[AutomationJobRecord], db: Optional[Session] = None) -> Optional[DownloadTask]:
    """读取字幕阶段底层任务，优先用阶段 task_id，兼容历史数据回退到父任务查询"""
    if not job or not db:
        return None
    stage = _stage_by_key(job, "subtitle")
    task_id = stage.get("task_id") if stage else None
    if task_id:
        task = db.query(DownloadTask).filter(DownloadTask.id == int(task_id)).first()
        if task:
            return task
    return (
        db.query(DownloadTask)
        .filter(DownloadTask.parent_job_id == job.id)
        .filter(DownloadTask.task_type == "subtitle")
        .order_by(DownloadTask.created_at.desc())
        .first()
    )


def _latest_matching_file(directory: str, pattern: str) -> Optional[str]:
    """按修改时间取最新匹配文件，兼容旧任务没有保存字幕资产路径的情况"""
    candidates = [path for path in glob(os.path.join(directory, pattern)) if os.path.isfile(path)]
    if not candidates:
        return None
    candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return candidates[0]


def _legacy_stage_paths_from_media(media_path: Optional[str]) -> Optional[dict[str, str]]:
    """兼容旧版平铺目录任务，从 downloads/output/exports 路径回推出旧项目目录结构"""
    raw_path = str(media_path or "").strip()
    if not raw_path:
        return None
    normalized = os.path.abspath(os.path.expanduser(raw_path))
    stage_dir = os.path.dirname(normalized)
    stage_name = os.path.basename(stage_dir).lower()
    if stage_name not in {"downloads", "output", "exports"}:
        return None
    project_root = os.path.dirname(stage_dir)
    if not project_root:
        return None
    return {
        "project_root": project_root,
        "downloads_dir": os.path.join(project_root, "downloads"),
        "output_dir": os.path.join(project_root, "output"),
        "exports_dir": os.path.join(project_root, "exports"),
    }


def _subtitle_search_dirs(job: Optional[AutomationJobRecord], db: Optional[Session], source_video_path: Optional[str], subtitle_stage_output: Optional[str]) -> list[str]:
    """收集可编辑字幕的候选目录，兼容新工作目录和旧版平铺 output 目录"""
    directories: list[str] = []

    def add_directory(path: Optional[str]) -> None:
        """去重追加候选目录，只保留真实存在的目录"""
        normalized = str(path or "").strip()
        if not normalized:
            return
        directory = os.path.abspath(os.path.expanduser(normalized))
        if not os.path.isdir(directory) or directory in directories:
            return
        directories.append(directory)

    workspace_paths = _job_workspace_paths(job, db)
    if workspace_paths:
        add_directory(workspace_paths.get("output_dir"))

    for candidate in (
        subtitle_stage_output,
        source_video_path,
        _stage_output_if_reusable(job, "download"),
        _stage_output_if_reusable(job, "effects"),
        _stage_output_if_reusable(job, "export"),
    ):
        if not candidate:
            continue
        detected = detect_video_workspace(candidate)
        if detected:
            add_directory(detected.get("output_dir"))
        legacy_paths = _legacy_stage_paths_from_media(candidate)
        if legacy_paths:
            add_directory(legacy_paths.get("output_dir"))
        parent_dir = os.path.dirname(os.path.abspath(os.path.expanduser(candidate)))
        if os.path.basename(parent_dir).lower() == "output":
            add_directory(parent_dir)

    add_directory(ensure_project_dirs().get("output_dir"))
    return directories


def _subtitle_search_basenames(source_video_path: Optional[str], subtitle_stage_output: Optional[str]) -> list[str]:
    """生成字幕回溯时使用的基础文件名，兼容 subtitled/final 等旧后缀"""
    basenames: list[str] = []

    def add_basename(name: Optional[str]) -> None:
        value = str(name or "").strip()
        if value and value not in basenames:
            basenames.append(value)

    if source_video_path:
        add_basename(os.path.splitext(os.path.basename(source_video_path))[0])

    if subtitle_stage_output:
        subtitle_base = os.path.splitext(os.path.basename(subtitle_stage_output))[0]
        add_basename(subtitle_base)
        lowered = subtitle_base.lower()
        for suffix in ("_subtitled", "_voiced", "_manual_final", "_final", "_enhanced", "_preview"):
            if lowered.endswith(suffix):
                add_basename(subtitle_base[: -len(suffix)].rstrip("._- "))
                break

    return basenames


def _find_job_source_video_path(job: Optional[AutomationJobRecord]) -> Optional[str]:
    """推导重新合成导出要使用的源视频，优先画面处理产物，再回退下载原片"""
    params = _get_job_params(job) if job else {}
    for key in ("source_video_path", "downloaded_video_path"):
        candidate = _existing_file(str(params.get(key) or ""), MEDIA_EXTENSIONS)
        if candidate:
            return candidate
    return (
        _stage_output_if_reusable(job, "effects")
        or _stage_output_if_reusable(job, "download")
    )


def _find_job_voice_asset_path(job: Optional[AutomationJobRecord]) -> Optional[str]:
    """推导配音音轨路径"""
    return _stage_output_if_reusable(job, "voice")


def _find_job_editable_subtitle_path(job: Optional[AutomationJobRecord], db: Optional[Session] = None) -> Optional[str]:
    """推导可重新编辑的字幕文件路径，优先任务显式记录，再兼容旧任务按命名规则回溯"""
    if not job:
        return None

    params = _get_job_params(job)
    manual_override = _existing_file(str(params.get("manual_subtitle_asset_path") or ""), EDITABLE_SUBTITLE_EXTENSIONS)
    if manual_override:
        return manual_override

    subtitle_stage = _stage_by_key(job, "subtitle")
    stage_output = _existing_file(str((subtitle_stage or {}).get("output_path") or ""), EDITABLE_SUBTITLE_EXTENSIONS)
    if stage_output:
        return stage_output

    subtitle_task = _subtitle_task_record(job, db)
    task_params = _read_task_params(subtitle_task)
    for key in ("editable_subtitle_path", "subtitle_ass_path", "source_subtitle_path", "subtitle_path"):
        candidate = _existing_file(str(task_params.get(key) or ""), EDITABLE_SUBTITLE_EXTENSIONS)
        if candidate:
            return candidate

    source_video_path = _find_job_source_video_path(job)
    subtitle_stage_output = _existing_file(str((subtitle_stage or {}).get("output_path") or ""))
    search_dirs = _subtitle_search_dirs(job, db, source_video_path, subtitle_stage_output)
    search_basenames = _subtitle_search_basenames(source_video_path, subtitle_stage_output)
    if not search_dirs or not search_basenames:
        return None

    for directory in search_dirs:
        for base_name in search_basenames:
            for pattern in (
                f"{base_name}.ass",
                f"{base_name}.srt",
                f"{base_name}.vtt",
                f"{base_name}_*.ass",
                f"{base_name}_*.srt",
                f"{base_name}_*.vtt",
            ):
                matched = _latest_matching_file(directory, pattern)
                if matched:
                    return matched
    return None


def _store_job_workspace_params(job: Optional[AutomationJobRecord], paths: dict[str, str], source_video_path: Optional[str] = None) -> None:
    """把视频工作目录写回任务参数，后续重导出和字幕页都直接按目录找资源"""
    if not job:
        return
    params = _get_job_params(job)
    params.update({
        "workspace_dir": paths.get("workspace_dir"),
        "workspace_name": paths.get("workspace_name"),
        "video_downloads_dir": paths.get("downloads_dir"),
        "video_output_dir": paths.get("output_dir"),
        "video_exports_dir": paths.get("exports_dir"),
    })
    if source_video_path:
        params["source_video_path"] = source_video_path
    _set_job_params(job, params)


def _job_workspace_paths(job: Optional[AutomationJobRecord], db: Optional[Session] = None) -> Optional[dict[str, str]]:
    """读取任务绑定的视频工作目录，旧任务则尽量从现有文件路径或视频信息推导"""
    if not job:
        return None
    params = _get_job_params(job)
    workspace_dir = str(params.get("workspace_dir") or "").strip()
    if workspace_dir:
        return {
            "workspace_dir": workspace_dir,
            "workspace_name": str(params.get("workspace_name") or os.path.basename(workspace_dir)),
            "downloads_dir": str(params.get("video_downloads_dir") or os.path.join(workspace_dir, "downloads")),
            "output_dir": str(params.get("video_output_dir") or os.path.join(workspace_dir, "output")),
            "exports_dir": str(params.get("video_exports_dir") or os.path.join(workspace_dir, "exports")),
        }

    for candidate in (
        _stage_output_if_reusable(job, "download"),
        _stage_output_if_reusable(job, "effects"),
        _stage_output_if_reusable(job, "subtitle"),
        _stage_output_if_reusable(job, "voice"),
        _stage_output_if_reusable(job, "export"),
        _existing_file(str(params.get("source_video_path") or ""), MEDIA_EXTENSIONS),
    ):
        if not candidate:
            continue
        detected = detect_video_workspace(candidate)
        if detected:
            return detected

    if db and job.video_id:
        video = db.query(VideoSource).filter(VideoSource.id == job.video_id).first()
        if video:
            return ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
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


def _pause_task(db: Session, task: DownloadTask) -> None:
    """标记底层任务暂停"""
    task.status = "paused"
    task.error_message = "用户暂停，等待继续"
    db.commit()


def _cancel_task(db: Session, task: DownloadTask) -> None:
    """标记底层任务取消"""
    task.status = CANCELLED_STATUS
    task.error_message = "用户取消"
    task.completed_at = datetime.now()
    db.commit()


def _fail_task(db: Session, task: DownloadTask, error: Exception) -> None:
    """标记任务失败并保存错误"""
    task.status = "failed"
    task.error_message = str(error)
    db.commit()


def _handle_task_control(db: Session, task: DownloadTask, exc: TaskControlRequested) -> None:
    """根据用户控制动作更新底层任务状态"""
    if exc.action == "pause":
        _pause_task(db, task)
    else:
        _cancel_task(db, task)


def _update_export_progress(db: Session, job: Optional[AutomationJobRecord], task: DownloadTask, progress: float) -> None:
    """同步导出阶段的细分进度"""
    _check_control(db, job, task)
    task.progress = max(0.0, min(99.0, progress))
    db.commit()
    if job:
        _update_job_stage(db, job, "export", "running", progress=task.progress, task_id=task.id)


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


def _normalize_subtitle_language(language: Optional[str]) -> str:
    """清理字幕语言代码，auto 表示交给候选逻辑处理"""
    value = str(language or "").strip()
    return "" if value == "auto" else value


def _subtitle_language_variants(language: Optional[str]) -> list[str]:
    """扩展常见中文字幕语言代码，避免 zh-Hans 和 zh-CN 互相漏选"""
    value = _normalize_subtitle_language(language)
    if not value:
        return []

    variants = [value]
    if value.lower().startswith("zh"):
        variants.extend(["zh-CN", "zh-Hans", "zh"])

    result: list[str] = []
    for item in variants:
        if item and item not in result:
            result.append(item)
    return result


def _build_subtitle_download_candidates(
    video: VideoSource,
    requested_language: Optional[str],
    preset_language: Optional[str],
) -> list[dict[str, str]]:
    """生成字幕下载候选，按语言和字幕类型去重并保留降级顺序"""
    tracks = _load_subtitle_tracks(video)
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(language: Optional[str], sub_type: Optional[str] = None) -> None:
        """加入一个候选语言，重复项只保留第一次出现的优先级"""
        normalized_language = _normalize_subtitle_language(language)
        if not normalized_language:
            return
        normalized_type = str(sub_type or "auto").strip() or "auto"
        key = (normalized_language, normalized_type)
        if key in seen:
            return
        seen.add(key)
        candidates.append({"language": normalized_language, "sub_type": normalized_type})

    def add_matching_tracks(language: str) -> bool:
        """优先加入解析阶段已确认存在的同语言字幕轨"""
        matched = [track for track in tracks if track.get("language") == language]
        matched.sort(key=lambda track: 0 if track.get("type") == "original" else 1)
        for track in matched:
            add_candidate(track.get("language"), track.get("type"))
        return bool(matched)

    primary_languages = (
        _subtitle_language_variants(requested_language)
        + _subtitle_language_variants(preset_language)
        + list(SUBTITLE_FALLBACK_LANGUAGES)
    )
    for language in primary_languages:
        if not add_matching_tracks(language):
            add_candidate(language, "auto")

    # 已上传字幕通常比自动翻译字幕更稳定，首选语言失败后优先尝试这些轨道。
    sorted_tracks = sorted(tracks, key=lambda track: 0 if track.get("type") == "original" else 1)
    for track in sorted_tracks:
        if track.get("type") == "original" or track.get("language") in SUBTITLE_FALLBACK_LANGUAGES:
            add_candidate(track.get("language"), track.get("type"))

    return candidates[:SUBTITLE_MAX_DOWNLOAD_CANDIDATES]


def _format_subtitle_fallback_error(errors: list[str]) -> str:
    """压缩字幕下载降级错误，避免前端被 yt-dlp 长日志刷屏"""
    if not errors:
        return "未知错误"
    preview = errors[:4]
    if len(errors) > len(preview):
        preview.append(f"还有 {len(errors) - len(preview)} 个候选失败")
    return "；".join(preview)


def _download_subtitle_with_fallback(
    downloader: Downloader,
    video: VideoSource,
    requested_language: Optional[str],
    preset_language: Optional[str],
    output_dir: str,
    control_keys: Optional[list[str]],
) -> tuple[str, str, list[str]]:
    """按候选字幕轨逐个下载，首选语言被限流时自动降级到其它可用轨"""
    candidates = _build_subtitle_download_candidates(video, requested_language, preset_language)
    errors: list[str] = []

    for candidate in candidates:
        language = candidate["language"]
        sub_type = candidate["sub_type"]
        try:
            subtitle_path = downloader.download_subtitle(
                url=video.url,
                language=language,
                output_dir=output_dir,
                sub_type=sub_type,
                control_keys=control_keys,
            )
            return subtitle_path, language, errors
        except TaskControlRequested:
            raise
        except Exception as exc:
            message = " ".join(str(exc).split())
            errors.append(f"{language}/{sub_type}: {message}")

    attempted = "、".join(dict.fromkeys(candidate["language"] for candidate in candidates))
    raise RuntimeError(f"字幕下载失败，已尝试 {attempted or '无可用候选'}: {_format_subtitle_fallback_error(errors)}")


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
    """选择指定或首个文本配置"""
    if not profile_id:
        return db.query(TextProviderProfile).order_by(TextProviderProfile.id.asc()).first()
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


def _normalize_glossary_terms(terms: list[dict[str, Any]]) -> list[dict[str, str]]:
    """清理专业术语字库，保留原词、固定写法和备注"""
    normalized: list[dict[str, str]] = []
    for item in terms or []:
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        normalized.append({
            "source": source,
            "replacement": str(item.get("replacement") or "").strip(),
            "note": str(item.get("note") or "").strip(),
        })
    return normalized


def _apply_glossary_terms(entries: list[dict[str, Any]], terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把术语字库应用到字幕条目，保证固定写法进入字幕和配音"""
    glossary = _normalize_glossary_terms(terms)
    if not glossary:
        return entries

    processed: list[dict[str, Any]] = []
    for entry in entries:
        next_entry = dict(entry)
        text = str(next_entry.get("text") or "")
        for term in glossary:
            replacement = term.get("replacement") or term["source"]
            text = text.replace(term["source"], replacement)
        next_entry["text"] = text
        processed.append(next_entry)
    return processed


def _glossary_prompt_suffix(terms: list[dict[str, Any]]) -> str:
    """把术语字库整理成文本 API 可读的系统提示补充"""
    glossary = _normalize_glossary_terms(terms)
    if not glossary:
        return ""
    lines = []
    for term in glossary:
        replacement = term.get("replacement") or "保持原词"
        note = f"，备注：{term['note']}" if term.get("note") else ""
        lines.append(f"- {term['source']} => {replacement}{note}")
    return "\n\n术语字库要求：\n" + "\n".join(lines)


def _find_banned_words(text: str, banned_words: list[str]) -> list[str]:
    """检测字幕或配音文案里的禁词，返回命中的去重列表"""
    if not text:
        return []
    hits: list[str] = []
    for word in banned_words or []:
        normalized = str(word or "").strip()
        if normalized and normalized in text and normalized not in hits:
            hits.append(normalized)
    return hits


def _extract_speaker_from_text(text: str) -> tuple[Optional[str], str]:
    """从字幕文本里提取“说话人：正文”结构"""
    normalized = str(text or "").strip()
    if not normalized:
        return None, ""
    for separator in ("：", ":", " - ", "-"):
        if separator not in normalized:
            continue
        speaker, content = normalized.split(separator, 1)
        speaker = speaker.strip()
        content = content.strip()
        if speaker and content and len(speaker) <= 24:
            return speaker, content
    return None, normalized


def _voice_for_segment(segment: dict[str, Any], default_voice: str, speaker_voice_map: dict[str, str]) -> str:
    """按说话人标签选择分段配音音色"""
    speaker = str(segment.get("speaker") or "").strip()
    if not speaker:
        return default_voice
    return speaker_voice_map.get(speaker) or speaker_voice_map.get(speaker.lower()) or default_voice


def _voice_output_extension(provider_type: str, settings: dict[str, Any]) -> str:
    """按配音渠道和设置决定输出音频扩展名"""
    value = str(settings.get("format") or "").lower()
    if value:
        return "wav" if value == "pcm16" else value
    return "wav" if provider_type == "xiaomi_mimo_tts" else "mp3"


def _srt_time_to_milliseconds(value: str) -> int:
    """把 SRT/VTT 时间码转换成毫秒"""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        return 0
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return 0
    return int(round((hours * 3600 + minutes * 60 + seconds) * 1000))


def subtitle_entries_to_voice_segments(entries: list[dict[str, Any]], max_chars_per_segment: int = 220) -> list[dict[str, Any]]:
    """把字幕条目转换为配音分段，保留每段在视频里的起止时间"""
    segments: list[dict[str, Any]] = []
    for entry in entries:
        raw_text = " ".join(str(entry.get("text") or "").replace("\\N", " ").split())
        speaker, text = _extract_speaker_from_text(raw_text)
        if not text:
            continue
        start_ms = _srt_time_to_milliseconds(str(entry.get("start", "00:00:00,000")))
        end_ms = _srt_time_to_milliseconds(str(entry.get("end", "00:00:00,000")))
        if end_ms <= start_ms:
            end_ms = start_ms + 1000

        # 单条字幕过长时按文本长度拆成小段，时间按比例均分，避免单次 TTS 输入过大。
        if len(text) <= max_chars_per_segment:
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text, "speaker": speaker})
            continue

        parts = [text[index:index + max_chars_per_segment] for index in range(0, len(text), max_chars_per_segment)]
        duration = max(1, end_ms - start_ms)
        for index, part in enumerate(parts):
            part_start = start_ms + int(duration * index / len(parts))
            part_end = start_ms + int(duration * (index + 1) / len(parts))
            segments.append({"start_ms": part_start, "end_ms": max(part_start + 1, part_end), "text": part.strip(), "speaker": speaker})
    return segments


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

    if line_count < source_count:
        distributed_lines = _distribute_text_to_original_slots(lines, original_entries)
        for index, entry in enumerate(original_entries, 1):
            entries.append({
                "index": index,
                "start": str(entry.get("start", "00:00:00,000")),
                "end": str(entry.get("end", "00:00:01,000")),
                "text": distributed_lines[index - 1] or str(entry.get("text") or ""),
            })
        return entries

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


def _distribute_text_to_original_slots(lines: list[str], original_entries: list[dict]) -> list[str]:
    """把少行文本拆回原字幕槽位，避免整段翻译兜底破坏本地识别时间轴"""
    text = _join_processed_lines(lines)
    if not text:
        return ["" for _ in original_entries]

    weights = [
        max(1, len(str(entry.get("text") or "").replace("\\N", " ").strip()))
        for entry in original_entries
    ]
    units = _mapping_text_units(text, len(original_entries))
    if not units:
        return ["" for _ in original_entries]

    total_units = len(units)
    total_weight = max(1, sum(weights))
    distributed: list[str] = []
    unit_start = 0
    elapsed_weight = 0
    for index, weight in enumerate(weights):
        elapsed_weight += weight
        unit_end = total_units if index == len(weights) - 1 else round(total_units * elapsed_weight / total_weight)
        unit_end = max(unit_start + 1, min(total_units, unit_end))
        distributed.append(_join_mapping_units(units[unit_start:unit_end]))
        unit_start = unit_end
        if unit_start >= total_units:
            distributed.extend(["" for _ in range(index + 1, len(weights))])
            break
    return distributed[:len(original_entries)]


def _join_processed_lines(lines: list[str]) -> str:
    """合并文本 API 整段兜底结果，中文不额外加空格，英文按词保留空格"""
    text = ""
    for line in lines:
        cleaned = " ".join(str(line or "").split())
        if not cleaned:
            continue
        if not text:
            text = cleaned
            continue
        separator = " " if re.match(r"[\w\]]$", text, re.ASCII) and re.match(r"^[\w\[]", cleaned, re.ASCII) else ""
        text = f"{text}{separator}{cleaned}"
    return text


def _mapping_text_units(text: str, target_count: int) -> list[str]:
    """生成用于回填时间轴的文本单元，英文优先按词，中文优先按字"""
    normalized = " ".join(str(text or "").split())
    tokens = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s]", normalized)
    tokens = _attach_mapping_punctuation(tokens)
    if len(tokens) >= max(2, target_count // 2):
        return tokens
    return [char for char in normalized if not char.isspace()]


def _attach_mapping_punctuation(tokens: list[str]) -> list[str]:
    """把标点并到相邻文本单元，避免兜底回填时生成只有标点的字幕"""
    units: list[str] = []
    pending_prefix = ""
    closing_marks = "，。、！？；,.!?;:：)]}）】”’》"
    opening_marks = "([{（【“‘《"
    for token in tokens:
        if not token.strip():
            continue
        if token in opening_marks:
            pending_prefix += token
            continue
        if token in closing_marks and units:
            units[-1] = f"{units[-1]}{token}"
            continue
        units.append(f"{pending_prefix}{token}")
        pending_prefix = ""
    if pending_prefix and units:
        units[-1] = f"{units[-1]}{pending_prefix}"
    return units


def _join_mapping_units(units: list[str]) -> str:
    """合并回填文本单元，中文连续显示，英文单词之间保留空格"""
    if not units:
        return ""
    text = ""
    for unit in [item.strip() for item in units if item.strip()]:
        if not text:
            text = unit
            continue
        separator = " " if _mapping_units_need_space(text[-1], unit[0]) else ""
        text = f"{text}{separator}{unit}"
    return text.strip()


def _mapping_units_need_space(left: str, right: str) -> bool:
    """判断字幕回填单元之间是否需要空格，避免切碎英文同时不破坏中文显示"""
    if not left or not right:
        return False
    if right in "，。、！？；,.!?;:：)]}）】”’":
        return False
    if left in "([{（【“‘":
        return False
    return bool(re.match(r"[A-Za-z0-9]", left) and re.match(r"[A-Za-z0-9]", right))


def combine_original_and_translated_entries(original_entries: list[dict], translated_entries: list[dict]) -> list[dict]:
    """把译文放到主字幕、原文放到第二行，配音仍单独使用译文条目"""
    if not original_entries or not translated_entries:
        return translated_entries

    combined: list[dict] = []
    for index, translated in enumerate(translated_entries):
        original = original_entries[min(index, len(original_entries) - 1)]
        original_text = " ".join(str(original.get("text") or "").replace("\\N", " ").split())
        translated_text = " ".join(str(translated.get("text") or "").replace("\\N", " ").split())
        next_entry = dict(translated)
        if original_text and translated_text and original_text != translated_text:
            next_entry["text"] = f"{translated_text}\n{original_text}"
        elif translated_text:
            next_entry["text"] = translated_text
        else:
            next_entry["text"] = original_text
        combined.append(next_entry)
    return combined


def merge_subtitle_burn_preset(subtitle_preset: dict[str, Any], processing_preset: dict[str, Any]) -> dict[str, Any]:
    """把字幕样式和导出质量参数合并，保证跳过画面处理时字幕烧录仍按输出策略控体积"""
    merged = dict(subtitle_preset or {})
    if isinstance(processing_preset, dict):
        if isinstance(processing_preset.get("bitrate"), dict):
            merged["bitrate"] = processing_preset["bitrate"]
        if isinstance(processing_preset.get("acceleration"), dict):
            merged["acceleration"] = processing_preset["acceleration"]
    return merged


def _prepare_subtitle_for_burn(
    subtitle_path: str,
    output_dir: str,
    preset: dict[str, Any],
    suffix: str = "clean",
) -> str:
    """烧录前统一清理字幕文件，避免旧 ASS/SRT 绕过标点过滤"""
    engine = SubtitleEngine()
    entries = _parse_subtitle_entries(engine, subtitle_path)
    if not entries:
        raise RuntimeError("字幕文件为空或无法解析，不能重新烧录")
    display_entries = engine.normalize_entries_for_display(entries, preset)
    base_name = os.path.splitext(os.path.basename(subtitle_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_{suffix}.ass")
    engine.generate_ass(display_entries, output_path, preset)
    return output_path


def _subtitle_preset_dict_for_export(db: Session, job_params: dict[str, Any]) -> dict[str, Any]:
    """重新导出时读取字幕样式；测试桩或旧数据异常时回退默认清理样式"""
    preset_id = job_params.get("subtitle_preset_id")
    try:
        preset = _pick_subtitle_preset(db, int(preset_id)) if preset_id else _pick_subtitle_preset(db, None)
    except Exception:
        return {}
    if not preset or not hasattr(preset, "line_mode"):
        return {}
    return _preset_to_dict(preset)


def build_final_export_preset(export_settings: dict[str, Any]) -> dict[str, Any]:
    """生成最终导出专用预设，只保留导出阶段需要的分辨率和码率参数"""
    resolution = str(export_settings.get("resolution") or "original")
    canvas_enabled = resolution != "original"
    canvas = {
        "enabled": canvas_enabled,
        "resolution": resolution if resolution in {"720p", "1080p", "custom"} else "original",
        "mode": "keep",
        "width": int(export_settings.get("width") or 1920),
        "height": int(export_settings.get("height") or 1080),
    }
    bitrate_enabled = bool(export_settings.get("bitrate_enabled"))
    bitrate_kbps = max(0, int(export_settings.get("bitrate_kbps") or 0))
    bitrate = {
        "enabled": bitrate_enabled,
        "mode": "fixed",
        "fixed_kbps": {"enabled": bitrate_enabled, "random": False, "value": bitrate_kbps, "min": bitrate_kbps, "max": bitrate_kbps},
    }
    acceleration = {"enabled": True, "mode": "auto", "quality": "size"}
    return {
        "adjustments": {"enabled": False},
        "canvas": canvas,
        "transform": {
            "enabled": False,
            "rotate_mode": "none",
            "flip_horizontal": False,
            "flip_vertical": False,
            "random_rotate": {"enabled": False, "random": False, "value": 0, "min": 0, "max": 0},
            "remove_black_bars": False,
            "show_full_frame": True,
        },
        "timing": {
            "enabled": False,
            "fps": {"enabled": False, "random": False, "value": 30, "min": 30, "max": 30},
            "drop_frame": {"enabled": False, "interval": {"enabled": False, "random": False, "value": 25, "min": 25, "max": 25}},
            "dynamic_zoom": {"enabled": False, "random": False, "value": 0, "min": 0, "max": 0},
        },
        "bitrate": bitrate,
        "acceleration": acceleration,
    }


def should_apply_final_export_settings(export_with_settings: bool, export_settings: dict[str, Any]) -> bool:
    """判断是否需要在导出末尾再按导出设置统一输出一次"""
    if not export_with_settings or not isinstance(export_settings, dict):
        return False

    if bool(export_settings.get("bitrate_enabled")) and int(export_settings.get("bitrate_kbps") or 0) > 0:
        return True

    resolution = str(export_settings.get("resolution") or "original")
    if resolution in {"720p", "1080p"}:
        return True
    return resolution == "custom" and int(export_settings.get("width") or 0) > 0 and int(export_settings.get("height") or 0) > 0


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
    assert_required_tools_available()
    downloader = Downloader()
    processor = FFmpegProcessor()
    stages: list[AutomationStageResult] = []
    subtitle_text = ""
    subtitle_entries: list[dict[str, Any]] = []
    subtitle_ass_path: Optional[str] = None
    subtitle_burn_preset: dict[str, Any] = merge_subtitle_burn_preset({}, request.processing_preset)
    final_export_preset: dict[str, Any] = build_final_export_preset(request.export_settings)
    audio_path: Optional[str] = None
    warning_messages: list[str] = []
    preset_dict: dict[str, Any] = {}

    video = _parse_or_update_video(db, request.url, downloader)
    paths = ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
    _check_control(db, job)
    stages.append(AutomationStageResult(key="parse", status="completed"))
    if job:
        job.video_id = video.id
        job.title = video.title or "一键自动流程"
        job.status = "running"
        _store_job_workspace_params(job, paths)
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
            _check_control(db, job, download_task)
            downloaded_path = downloader.download_video(
                url=video.url,
                output_dir=paths["downloads_dir"],
                format_id=request.format_id,
                output_format="mp4",
                progress_callback=on_progress,
                control_keys=_control_keys(job, download_task),
            )
            _check_control(db, job, download_task)
            _complete_task(db, download_task, downloaded_path)
            if job:
                _store_job_workspace_params(job, paths, source_video_path=downloaded_path)
            stages.append(AutomationStageResult(key="download", status="completed", task_id=download_task.id, output_path=downloaded_path))
            if job:
                _update_job_stage(db, job, "download", "completed", task_id=download_task.id, output_path=downloaded_path)
        except TaskControlRequested as exc:
            _handle_task_control(db, download_task, exc)
            raise
        except Exception as exc:
            _fail_task(db, download_task, exc)
            if job:
                _update_job_stage(db, job, "download", "failed", task_id=download_task.id, error_message=str(exc))
            raise

    reusable_effects_path = _stage_output_if_reusable(job, "effects") if resume_from_checkpoint else None
    if not request.enable_effects:
        effects_path = downloaded_path
        stages.append(AutomationStageResult(key="effects", status="skipped", progress=100, output_path=effects_path, error_message="已按设置跳过画面处理"))
        if job:
            _update_job_stage(db, job, "effects", "skipped", output_path=effects_path, error_message="已按设置跳过画面处理")
    elif reusable_effects_path:
        effects_path = reusable_effects_path
        _mark_stage_reused(db, job, "effects", effects_path)
        stages.append(AutomationStageResult(key="effects", status="completed", progress=100, output_path=effects_path))
    else:
        effects_task = _create_task(db, video.id, "effects", {"preset": request.processing_preset}, job.id if job else None)
        if job:
            _update_job_stage(db, job, "effects", "running", progress=15, task_id=effects_task.id)
        try:
            _check_control(db, job, effects_task)

            def on_effects_progress(progress: float) -> None:
                """同步画面处理进度，避免长视频重编码看起来卡住"""
                _check_control(db, job, effects_task)
                stage_progress = min(95.0, 15.0 + progress * 0.8)
                effects_task.progress = stage_progress
                db.commit()
                if job:
                    _update_job_stage(db, job, "effects", "running", progress=stage_progress, task_id=effects_task.id)

            effects_path = processor.apply_effects(
                video_path=downloaded_path,
                preset=request.processing_preset,
                control_keys=_control_keys(job, effects_task),
                progress_callback=on_effects_progress,
            )
            _check_control(db, job, effects_task)
            _complete_task(db, effects_task, effects_path)
            stages.append(AutomationStageResult(key="effects", status="completed", task_id=effects_task.id, output_path=effects_path))
            if job:
                _update_job_stage(db, job, "effects", "completed", task_id=effects_task.id, output_path=effects_path)
        except TaskControlRequested as exc:
            if exc.action == "skip":
                effects_path = downloaded_path
                effects_task.status = "completed"
                effects_task.progress = 100
                effects_task.output_path = effects_path
                effects_task.error_message = "用户跳过画面处理"
                db.commit()
                stages.append(AutomationStageResult(key="effects", status="skipped", task_id=effects_task.id, progress=100, output_path=effects_path, error_message=effects_task.error_message))
                if job:
                    params = _get_job_params(job)
                    params["enable_effects"] = False
                    params["skip_effects_requested"] = True
                    _set_job_params(job, params)
                    clear_control_request(_job_control_key(job.id))
                    clear_control_request(_task_control_key(effects_task.id))
                    _update_job_stage(db, job, "effects", "skipped", task_id=effects_task.id, output_path=effects_path, error_message=effects_task.error_message)
            else:
                _handle_task_control(db, effects_task, exc)
                raise
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
            _check_control(db, job, subtitle_task)
            preset = _pick_subtitle_preset(db, request.subtitle_preset_id)
            preset_dict = _preset_to_dict(preset)
            engine = SubtitleEngine()

            def on_asr_progress(progress: float) -> None:
                """同步本地语音识别进度"""
                _check_control(db, job, subtitle_task)
                subtitle_task.progress = min(35.0, 10.0 + progress * 0.25)
                db.commit()
                if job:
                    _update_job_stage(db, job, "subtitle", "running", progress=subtitle_task.progress, task_id=subtitle_task.id)

            entries, language = LocalSpeechRecognizer().transcribe_video(
                video_path=effects_path,
                progress_callback=on_asr_progress,
            )
            subtitle_path = os.path.join(paths["output_dir"], f"{video.video_id}_{language}_local.srt")
            engine.save_srt(entries, subtitle_path)
            _check_control(db, job, subtitle_task)
            if job:
                _update_job_stage(db, job, "subtitle", "running", progress=35, task_id=subtitle_task.id)
            if not entries:
                raise RuntimeError("本地字幕识别结果为空，无法继续自动字幕处理")
            original_entries_for_display = [dict(entry) for entry in entries]
            translated_entries_for_display: Optional[list[dict[str, Any]]] = None
            subtitle_text = entries_to_plain_text(entries)
            text_profile = _pick_text_profile(db, request.text_profile_id)
            if text_profile and request.subtitle_operation != "none":
                target_language = request.subtitle_target_language or ("zh-CN" if request.subtitle_operation == "translate" else "")
                try:
                    text_settings = _load_profile_settings(text_profile)
                    glossary_prompt = _glossary_prompt_suffix(request.glossary_terms)
                    if glossary_prompt:
                        text_settings["system_prompt"] = f"{text_settings.get('system_prompt') or '你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。'}{glossary_prompt}"
                    text_api_key = decrypt_api_key(text_profile.api_key_encrypted)
                    text_engine = TextEngine()

                    def on_text_progress(progress: float) -> None:
                        """同步文本 API 批处理进度到字幕阶段"""
                        _check_control(db, job, subtitle_task)
                        subtitle_task.progress = 35 + progress * 0.3
                        db.commit()
                        if job:
                            _update_job_stage(db, job, "subtitle", "running", progress=subtitle_task.progress, task_id=subtitle_task.id)

                    try:
                        processed_entries = asyncio.run(text_engine.process_subtitle_entries(
                            entries=entries,
                            provider_type=text_profile.provider_type,
                            api_key=text_api_key,
                            base_url=text_profile.base_url,
                            model=text_profile.model or "",
                            settings=text_settings,
                            operation=request.subtitle_operation,
                            target_language=target_language,
                            progress_callback=on_text_progress,
                        ))
                        if processed_entries:
                            entries = processed_entries
                            if request.subtitle_operation == "translate":
                                translated_entries_for_display = [dict(entry) for entry in entries]
                            subtitle_text = entries_to_plain_text(entries)
                    except TaskControlRequested:
                        raise
                    except Exception:
                        processed_text = asyncio.run(text_engine.process_text(
                            text=subtitle_text,
                            provider_type=text_profile.provider_type,
                            api_key=text_api_key,
                            base_url=text_profile.base_url,
                            model=text_profile.model or "",
                            settings=text_settings,
                            operation=request.subtitle_operation,
                            target_language=target_language,
                        ))
                        processed_entries = map_text_to_timed_entries(processed_text, entries)
                        if processed_entries:
                            entries = processed_entries
                            if request.subtitle_operation == "translate":
                                translated_entries_for_display = [dict(entry) for entry in entries]
                            subtitle_text = processed_text
                except TaskControlRequested:
                    raise
                except Exception as text_exc:
                    # 文本 API 是增强能力，失败时继续使用本地识别字幕完成主流程。
                    warning_messages.append(f"文本 API 处理失败，已使用本地识别字幕: {text_exc}")
                    pass
            entries = _apply_glossary_terms(entries, request.glossary_terms)
            subtitle_text = entries_to_plain_text(entries)
            banned_hits = _find_banned_words(subtitle_text, request.banned_words)
            if banned_hits:
                message = f"禁词命中: {', '.join(banned_hits)}"
                if request.banned_word_action == "block":
                    raise BannedWordsDetected(message)
                warning_messages.append(message)
            display_source_entries = (
                combine_original_and_translated_entries(original_entries_for_display, translated_entries_for_display)
                if translated_entries_for_display and str(preset_dict.get("line_mode") or "").lower() == "double"
                else entries
            )
            display_entries = engine.normalize_entries_for_display(display_source_entries, preset_dict)
            subtitle_burn_preset = merge_subtitle_burn_preset(preset_dict, request.processing_preset)
            subtitle_entries = entries
            if job:
                job.subtitle_text = subtitle_text
                _update_job_stage(db, job, "subtitle", "running", progress=70, task_id=subtitle_task.id)
            base_name = os.path.splitext(os.path.basename(effects_path))[0]
            subtitle_ass_path = os.path.join(paths["output_dir"], f"{base_name}_{language}.ass")
            engine.generate_ass(display_entries, subtitle_ass_path, preset_dict)
            if request.burn_subtitles:
                video_for_export = processor.burn_subtitles(
                    video_path=effects_path,
                    subtitle_path=subtitle_ass_path,
                    preset=subtitle_burn_preset,
                    control_keys=_control_keys(job, subtitle_task),
                )
            _check_control(db, job, subtitle_task)
            _complete_task(db, subtitle_task, video_for_export if request.burn_subtitles else subtitle_ass_path)
            if warning_messages:
                subtitle_task.error_message = "；".join(warning_messages)
                db.commit()
            subtitle_task.params = json.dumps({
                "source_subtitle_path": subtitle_path,
                "editable_subtitle_path": subtitle_ass_path,
                "rendered_video_path": video_for_export if request.burn_subtitles else None,
                "subtitle_language": language,
            }, ensure_ascii=False)
            db.commit()
            stages.append(AutomationStageResult(key="subtitle", status="completed", task_id=subtitle_task.id, output_path=subtitle_task.output_path))
            if job:
                _update_job_stage(db, job, "subtitle", "completed", task_id=subtitle_task.id, output_path=subtitle_task.output_path, error_message="；".join(warning_messages) if warning_messages else None)
        except TaskControlRequested as exc:
            _handle_task_control(db, subtitle_task, exc)
            raise
        except Exception as exc:
            if isinstance(exc, BannedWordsDetected):
                _fail_task(db, subtitle_task, exc)
                if job:
                    _update_job_stage(db, job, "subtitle", "failed", task_id=subtitle_task.id, error_message=str(exc))
                raise
            skip_message = f"{str(exc) or '本地字幕识别失败'}，已跳过字幕并继续导出"
            _complete_task(db, subtitle_task, None)
            subtitle_task.error_message = skip_message
            db.commit()
            stages.append(AutomationStageResult(key="subtitle", status="skipped", task_id=subtitle_task.id, error_message=skip_message))
            if job:
                _update_job_stage(db, job, "subtitle", "skipped", task_id=subtitle_task.id, error_message=skip_message)

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
            _check_control(db, job, voice_task)
            settings = _load_profile_settings(voice_profile)
            voice = settings.get("voice") or voice_profile.voice or "alloy"
            voice_text = (request.voice_text or subtitle_text or _fallback_voice_text(video)).strip()
            voice_text = entries_to_plain_text(_apply_glossary_terms(_plain_text_to_entries(voice_text), request.glossary_terms)) if voice_text else voice_text
            banned_hits = _find_banned_words(voice_text, request.banned_words)
            if banned_hits and request.banned_word_action == "block":
                raise BannedWordsDetected(f"配音文案命中禁词: {', '.join(banned_hits)}")
            output_ext = _voice_output_extension(voice_profile.provider_type, settings)
            audio_path = os.path.join(paths["output_dir"], f"{video.video_id}_voice.{output_ext}")
            voice_engine = VoiceEngine()
            api_key = decrypt_api_key(voice_profile.api_key_encrypted)
            model = voice_profile.voice or ""
            segments = subtitle_entries_to_voice_segments(subtitle_entries)
            if request.voice_mode == "segmented" and segments and not request.voice_text:
                try:
                    audio_path = os.path.join(paths["output_dir"], f"{video.video_id}_voice_timed.{output_ext}")

                    def on_voice_progress(progress: float) -> None:
                        """同步分段配音进度到后台任务"""
                        _check_control(db, job, voice_task)
                        voice_task.progress = progress
                        db.commit()
                        if job:
                            _update_job_stage(db, job, "voice", "running", progress=progress, task_id=voice_task.id)

                    audio_path = asyncio.run(voice_engine.generate_timed_voice_track(
                        segments=segments,
                        output_path=audio_path,
                        provider_type=voice_profile.provider_type,
                        voice=voice,
                        voice_selector=(lambda segment: _voice_for_segment(segment, voice, request.speaker_voice_map)) if request.multi_speaker_enabled else None,
                        api_key=api_key,
                        base_url=voice_profile.base_url,
                        model=model,
                        settings=settings,
                        progress_callback=on_voice_progress,
                    ))
                    _check_control(db, job, voice_task)
                except TaskControlRequested:
                    raise
                except Exception as segmented_exc:
                    if job:
                        _update_job_stage(db, job, "voice", "running", progress=20, task_id=voice_task.id, error_message=f"分段配音失败，回退整段配音: {segmented_exc}")
                    audio_path = os.path.join(paths["output_dir"], f"{video.video_id}_voice.{output_ext}")
                    audio_path = asyncio.run(voice_engine.generate_voice(
                        text=voice_text,
                        output_path=audio_path,
                        provider_type=voice_profile.provider_type,
                        voice=voice,
                        api_key=api_key,
                        base_url=voice_profile.base_url,
                        model=model,
                        settings=settings,
                    ))
                    _check_control(db, job, voice_task)
            else:
                audio_path = asyncio.run(voice_engine.generate_voice(
                    text=voice_text,
                    output_path=audio_path,
                    provider_type=voice_profile.provider_type,
                    voice=voice,
                    api_key=api_key,
                    base_url=voice_profile.base_url,
                    model=model,
                    settings=settings,
                ))
                _check_control(db, job, voice_task)
            _complete_task(db, voice_task, audio_path)
            stages.append(AutomationStageResult(key="voice", status="completed", task_id=voice_task.id, output_path=audio_path))
            if job:
                _update_job_stage(db, job, "voice", "completed", task_id=voice_task.id, output_path=audio_path)
        except TaskControlRequested as exc:
            _handle_task_control(db, voice_task, exc)
            raise
        except Exception as exc:
            _fail_task(db, voice_task, exc)
            if isinstance(exc, BannedWordsDetected):
                if job:
                    _update_job_stage(db, job, "voice", "failed", task_id=voice_task.id, error_message=str(exc))
                raise
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
        _check_control(db, job, export_task)
        working_video = video_for_export
        if subtitle_ass_path and not request.burn_subtitles:
            subtitle_burn_preset = merge_subtitle_burn_preset(preset_dict, request.processing_preset)
            working_video = processor.burn_subtitles(
                video_path=working_video,
                subtitle_path=subtitle_ass_path,
                preset=subtitle_burn_preset,
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(db, job, export_task, min(55.0, 15.0 + progress * 0.4)),
            )
            _check_control(db, job, export_task)
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
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(db, job, export_task, min(80.0, 55.0 + progress * 0.25)),
            )
            _check_control(db, job, export_task)
            export_task.progress = 70
            db.commit()
            if job:
                _update_job_stage(db, job, "export", "running", progress=70, task_id=export_task.id)
        if should_apply_final_export_settings(request.export_with_settings, request.export_settings):
            render_start = max(15.0, float(export_task.progress or 15.0))
            working_video = processor.apply_effects(
                video_path=working_video,
                preset=final_export_preset,
                output_path=os.path.join(paths["output_dir"], f"{os.path.splitext(os.path.basename(working_video))[0]}_final.mp4"),
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(
                    db,
                    job,
                    export_task,
                    min(92.0, render_start + progress * max(0.0, 92.0 - render_start) / 100.0),
                ),
            )
            _check_control(db, job, export_task)
            export_task.progress = 92
            db.commit()
            if job:
                _update_job_stage(db, job, "export", "running", progress=92, task_id=export_task.id)
        output_path = processor.convert_format(
            input_path=working_video,
            output_format=request.output_format,
            control_keys=_control_keys(job, export_task),
            progress_callback=lambda progress: _update_export_progress(
                db,
                job,
                export_task,
                min(95.0, max(80.0, float(export_task.progress or 80.0)) + progress * 0.15),
            ),
        )
        _check_control(db, job, export_task)
        _complete_task(db, export_task, output_path)
        stages.append(AutomationStageResult(key="export", status="completed", task_id=export_task.id, output_path=output_path))
        if job:
            job.output_path = output_path
            job.completed_at = datetime.now()
            _update_job_stage(db, job, "export", "completed", task_id=export_task.id, output_path=output_path)
            clear_job_control_requests(db, job)
            job.status = "completed"
            job.current_step = "流程完成"
            job.progress = 100
            db.commit()
    except TaskControlRequested as exc:
        _handle_task_control(db, export_task, exc)
        raise
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
    job: Optional[AutomationJobRecord] = None
    try:
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
        if not job:
            return
        if job.status in {"paused", CANCELLED_STATUS}:
            return
        job.status = "running"
        job.current_step = "准备自动处理"
        clear_job_control_requests(db, job)
        db.commit()

        params = json.loads(job.params or "{}")
        request = AutomationRunRequest(**params)
        _run_automation_sync(request, db, job, resume_from_checkpoint=resume_from_checkpoint)
    except TaskControlRequested as exc:
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
        if job:
            if exc.action == "pause":
                _pause_running_job(db, job)
            else:
                _cancel_job(db, job)
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
    clear_job_control_requests(None, job)
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
    clear_job_control_requests(None, job)
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("status") in {"running", "failed", "paused", CANCELLED_STATUS}:
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


def _prepare_interrupted_job_for_startup(job: AutomationJobRecord) -> None:
    """后端重启后恢复中断任务，尽量从已完成阶段后继续"""
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("status") in {"running", "failed", "paused", CANCELLED_STATUS}:
            stage["status"] = "pending"
            stage["progress"] = 0
            stage["task_id"] = None
            stage["output_path"] = None
            stage["error_message"] = None

    job.status = "pending"
    job.progress = _calculate_job_progress(stages)
    job.current_step = "后端重启后等待恢复"
    job.error_message = None
    job.completed_at = None
    job.stages = json.dumps(stages, ensure_ascii=False)


def _prepare_job_export_stage_for_rerun(job: AutomationJobRecord) -> None:
    """只重置导出阶段，供字幕调整页重新合成导出使用"""
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("key") != "export":
            continue
        stage["status"] = "pending"
        stage["progress"] = 0
        stage["task_id"] = None
        stage["output_path"] = None
        stage["error_message"] = None
        break
    job.status = "running"
    job.current_step = "字幕调整重新导出"
    job.output_path = None
    job.error_message = None
    job.completed_at = None
    job.stages = json.dumps(stages, ensure_ascii=False)
    job.progress = _calculate_job_progress(stages)


def _pause_running_job(db: Session, job: AutomationJobRecord, message: str = "用户暂停，等待继续") -> None:
    """把正在运行的一键流程标记为暂停"""
    mark_job_child_tasks_controlled(db, job, "paused", message)
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("status") in {"running", "pending"}:
            was_running = stage.get("status") == "running"
            stage["status"] = "paused" if was_running else "pending"
            if was_running:
                stage["error_message"] = message
            break
    job.status = "paused"
    job.current_step = "已暂停"
    job.error_message = message
    job.stages = json.dumps(stages, ensure_ascii=False)
    job.progress = _calculate_job_progress(stages)
    db.commit()


def _cancel_job(db: Session, job: AutomationJobRecord, message: str = "用户取消") -> None:
    """把一键流程标记为取消"""
    mark_job_child_tasks_controlled(db, job, CANCELLED_STATUS, message)
    stages = _load_job_stages(job)
    for stage in stages:
        if stage.get("status") in {"running", "pending", "paused"}:
            stage["status"] = CANCELLED_STATUS
            stage["error_message"] = message
    job.status = CANCELLED_STATUS
    job.current_step = "已取消"
    job.error_message = message
    job.completed_at = datetime.now()
    job.stages = json.dumps(stages, ensure_ascii=False)
    job.progress = _calculate_job_progress(stages)
    db.commit()


def _create_automation_job(db: Session, request: AutomationRunRequest, batch_id: Optional[str] = None, batch_concurrency: Optional[int] = None, title: str = "一键自动流程") -> AutomationJobRecord:
    """创建自动化任务记录"""
    job_id = f"auto-{uuid.uuid4().hex[:16]}"
    params = request.model_dump()
    if batch_id:
        params["batch_id"] = batch_id
        params["batch_concurrency"] = max(1, min(8, int(batch_concurrency or 2)))

    job = AutomationJobRecord(
        id=job_id,
        source_url=request.url,
        title=title,
        status="pending",
        progress=0,
        current_step="等待开始",
        stages=json.dumps(_default_stages(), ensure_ascii=False),
        params=json.dumps(params, ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    return job


def _get_batch_id_from_job(job: AutomationJobRecord) -> Optional[str]:
    """从任务参数里读取批次 ID"""
    if not job.params:
        return None
    try:
        params = json.loads(job.params)
    except json.JSONDecodeError:
        return None
    batch_id = params.get("batch_id")
    return batch_id if isinstance(batch_id, str) and batch_id else None


def _get_batch_concurrency_from_job(job: Optional[AutomationJobRecord]) -> int:
    """从任务参数里读取批次并发数，重启后恢复批次时使用"""
    if not job:
        return 2
    params = _get_job_params(job)
    try:
        return max(1, min(8, int(params.get("batch_concurrency") or 2)))
    except (TypeError, ValueError):
        return 2


def _current_running_stage(job: AutomationJobRecord) -> Optional[dict[str, Any]]:
    """读取当前正在运行的自动化阶段"""
    for stage in _load_job_stages(job):
        if stage.get("status") == "running":
            return stage
    return None


def _skip_current_effects_stage(db: Session, job: AutomationJobRecord) -> int:
    """请求跳过当前画面处理阶段，并只终止当前阶段 ffmpeg"""
    stage = _current_running_stage(job)
    if not stage or stage.get("key") != "effects":
        raise HTTPException(status_code=400, detail="当前只有画面处理阶段支持跳过")

    task_id = stage.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="画面处理任务尚未进入可跳过状态")

    task = db.query(DownloadTask).filter(DownloadTask.id == int(task_id)).first()
    if not task:
        raise HTTPException(status_code=404, detail="画面处理子任务不存在")

    killed_count = request_stage_task_control(db, task, "skip")
    if task.status in {"pending", "processing", "downloading", "paused"}:
        task.status = "skipped"
        task.error_message = "用户跳过画面处理"
    params = _get_job_params(job)
    params["enable_effects"] = False
    params["skip_effects_requested"] = True
    _set_job_params(job, params)
    job.current_step = "正在跳过画面处理"
    db.commit()
    return killed_count


def _get_job_params(job: AutomationJobRecord) -> dict[str, Any]:
    """安全读取自动化任务参数"""
    if not job.params:
        return {}
    try:
        params = json.loads(job.params)
        return params if isinstance(params, dict) else {}
    except json.JSONDecodeError:
        return {}


def _set_job_params(job: AutomationJobRecord, params: dict[str, Any]) -> None:
    """保存自动化任务参数 JSON"""
    job.params = json.dumps(params, ensure_ascii=False)


def _safe_media_file_path(path: str) -> str:
    """校验素材库播放器要读取的本地媒体文件路径"""
    media_path = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.exists(media_path) or not os.path.isfile(media_path):
        raise HTTPException(status_code=404, detail=f"媒体文件不存在: {media_path}")
    if os.path.splitext(media_path)[1].lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持播放常见音视频文件")
    return media_path


def _delete_job_record(db: Session, job: AutomationJobRecord) -> None:
    """删除一键流程记录和它的子任务记录，不删除硬盘上的成品文件"""
    for task in db.query(DownloadTask).filter(DownloadTask.parent_job_id == job.id).all():
        db.delete(task)
    db.delete(job)
    db.commit()


def _pause_batch_jobs(db: Session, batch_id: str) -> int:
    """暂停批次中尚未执行完成的自动化任务"""
    affected = 0
    jobs = db.query(AutomationJobRecord).order_by(AutomationJobRecord.created_at.asc()).all()
    for job in jobs:
        params = _get_job_params(job)
        if params.get("batch_id") != batch_id or job.status in TERMINAL_STATUSES:
            continue
        params["batch_paused"] = True
        _set_job_params(job, params)
        if job.status == "running":
            request_job_control(db, job, "pause")
            _pause_running_job(db, job, "批次暂停，等待恢复")
        elif job.status == "pending":
            job.status = "paused"
            job.current_step = "批次暂停"
        affected += 1
    db.commit()
    return affected


def _resume_batch_jobs(db: Session, batch_id: str) -> list[str]:
    """恢复批次任务，并返回需要重新提交到线程池的任务 ID"""
    job_ids: list[str] = []
    jobs = db.query(AutomationJobRecord).order_by(AutomationJobRecord.created_at.asc()).all()
    for job in jobs:
        params = _get_job_params(job)
        if params.get("batch_id") != batch_id:
            continue
        params["batch_paused"] = False
        _set_job_params(job, params)
        if job.status == "paused":
            job.status = "pending"
            job.current_step = "等待批次调度"
            job_ids.append(job.id)
    db.commit()
    return job_ids


def _is_batch_paused(batch_id: Optional[str]) -> bool:
    """读取内存中的批次暂停状态"""
    return bool(batch_id and batch_id in BATCH_PAUSED)


def _sync_paused_job_state(job_id: str, batch_id: Optional[str]) -> None:
    """等待批次恢复期间，把任务状态同步成暂停"""
    if not batch_id:
        return
    db = SessionLocal()
    try:
        job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
        if job and job.status in {"pending", "paused"}:
            job.status = "paused"
            job.current_step = "批次暂停"
            params = _get_job_params(job)
            params["batch_paused"] = True
            _set_job_params(job, params)
            db.commit()
    finally:
        db.close()


def _wait_until_batch_resumed(job_id: str, batch_id: Optional[str]) -> bool:
    """批次暂停时阻塞后台任务，恢复后继续调度"""
    if not batch_id:
        return True
    marked = False
    while _is_batch_paused(batch_id):
        if not marked:
            _sync_paused_job_state(job_id, batch_id)
            marked = True
        time.sleep(1)
    if marked:
        db = SessionLocal()
        try:
            job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
            if job and job.status == "paused":
                job.status = "pending"
                job.current_step = "等待批次调度"
                params = _get_job_params(job)
                params["batch_paused"] = False
                _set_job_params(job, params)
                db.commit()
        finally:
            db.close()
    return True


def _register_batch_semaphore(batch_id: str, concurrency: int) -> None:
    """注册批次并发控制器"""
    with BATCH_SEMAPHORE_LOCK:
        BATCH_SEMAPHORES[batch_id] = Semaphore(max(1, min(8, concurrency)))
        BATCH_PAUSED.discard(batch_id)


def _register_batch_pause(batch_id: str) -> None:
    """恢复持久化的批次暂停状态"""
    with BATCH_SEMAPHORE_LOCK:
        BATCH_PAUSED.add(batch_id)


def _restore_batch_runtime_state(jobs: list[AutomationJobRecord]) -> None:
    """根据持久化任务参数恢复批次并发和暂停状态"""
    for job in jobs:
        batch_id = _get_batch_id_from_job(job)
        if not batch_id:
            continue
        params = _get_job_params(job)
        with BATCH_SEMAPHORE_LOCK:
            if batch_id not in BATCH_SEMAPHORES:
                BATCH_SEMAPHORES[batch_id] = Semaphore(_get_batch_concurrency_from_job(job))
            if params.get("batch_paused") or job.status == "paused":
                BATCH_PAUSED.add(batch_id)


def _run_background_job_with_batch_limit(job_id: str, resume_from_checkpoint: bool = False) -> None:
    """后台线程入口，按批次并发限制执行任务"""
    try:
        db = SessionLocal()
        try:
            job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
            batch_id = _get_batch_id_from_job(job) if job else None
            batch_concurrency = _get_batch_concurrency_from_job(job)
        finally:
            db.close()

        _wait_until_batch_resumed(job_id, batch_id)
        semaphore = BATCH_SEMAPHORES.get(batch_id) if batch_id else None
        if batch_id and not semaphore:
            _register_batch_semaphore(batch_id, batch_concurrency)
            semaphore = BATCH_SEMAPHORES.get(batch_id)
        if not semaphore:
            _run_background_job(job_id, resume_from_checkpoint)
            return

        while True:
            _wait_until_batch_resumed(job_id, batch_id)
            semaphore.acquire()
            if _is_batch_paused(batch_id):
                semaphore.release()
                continue
            try:
                _run_background_job(job_id, resume_from_checkpoint)
            finally:
                semaphore.release()
            return
    finally:
        with SCHEDULED_JOB_LOCK:
            SCHEDULED_AUTOMATION_JOBS.discard(job_id)


def _submit_automation_job(job_id: str, resume_from_checkpoint: bool = False) -> None:
    """提交后台自动化任务，避免同一个任务被重复调度"""
    with SCHEDULED_JOB_LOCK:
        if job_id in SCHEDULED_AUTOMATION_JOBS:
            return
        SCHEDULED_AUTOMATION_JOBS.add(job_id)
    AUTOMATION_EXECUTOR.submit(_run_background_job_with_batch_limit, job_id, resume_from_checkpoint)


def recover_automation_jobs_on_startup() -> dict[str, int]:
    """后端启动后恢复未完成的一键自动化任务"""
    db = SessionLocal()
    try:
        jobs = db.query(AutomationJobRecord).filter(AutomationJobRecord.status.in_(["pending", "running", "paused"])).all()
        _restore_batch_runtime_state(jobs)

        submitted = 0
        paused = 0
        interrupted = 0
        for job in jobs:
            if job.status == "paused":
                paused += 1
                continue
            if job.status == "running":
                request_job_control(db, job, "cancel")
                _cancel_job(db, job, "后端重启前任务已中断，请点击继续重新执行")
                interrupted += 1
                continue
            elif job.status == "pending":
                job.current_step = job.current_step or "等待恢复"
            submitted += 1
            _submit_automation_job(job.id, True)
        db.commit()
        return {"submitted": submitted, "paused": paused, "interrupted": interrupted}
    finally:
        db.close()


def _normalize_batch_urls(urls: list[str]) -> list[str]:
    """清理批量链接：去空行、去重并保持原始顺序"""
    normalized_urls: list[str] = []
    seen_urls: set[str] = set()
    for raw_url in urls:
        url = raw_url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        normalized_urls.append(url)
    return normalized_urls


@router.post("/run", response_model=AutomationRunResponse)
def run_automation(request: AutomationRunRequest, db: Session = Depends(get_db)):
    """执行完整一键自动流程"""
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="请填写 YouTube 链接")

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = _create_automation_job(db, request)
    _submit_automation_job(job.id)
    return AutomationStartResponse(message="自动化任务已启动", job_id=job.id)


@router.post("/batch/start", response_model=AutomationBatchStartResponse)
def start_batch_automation(request: AutomationBatchStartRequest, db: Session = Depends(get_db)):
    """批量启动一键自动化任务"""
    normalized_urls = _normalize_batch_urls(request.urls)
    if not normalized_urls:
        raise HTTPException(status_code=400, detail="请至少填写一个有效链接")

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    _register_batch_semaphore(batch_id, request.concurrency)
    job_ids: list[str] = []
    for index, url in enumerate(normalized_urls, 1):
        job_request = request.template.model_copy(update={"url": url})
        job = _create_automation_job(db, job_request, batch_id=batch_id, batch_concurrency=request.concurrency, title=f"批量自动流程 {index}/{len(normalized_urls)}")
        job_ids.append(job.id)
        _submit_automation_job(job.id)

    return AutomationBatchStartResponse(
        message="批量自动化任务已进入队列",
        batch_id=batch_id,
        job_ids=job_ids,
        accepted_count=len(job_ids),
        skipped_count=max(0, len(request.urls) - len(job_ids)),
    )


@router.get("/jobs", response_model=list[AutomationJobResponse])
def list_automation_jobs(db: Session = Depends(get_db)):
    """获取自动化任务列表"""
    jobs = db.query(AutomationJobRecord).order_by(AutomationJobRecord.created_at.desc()).limit(50).all()
    return [_job_to_response(job, db) for job in jobs]


@router.get("/media")
def play_local_media(path: str = Query(..., min_length=1)):
    """用内置播放器读取本地成品视频或音频"""
    media_path = _safe_media_file_path(path)
    media_type = mimetypes.guess_type(media_path)[0] or "application/octet-stream"
    return FileResponse(media_path, media_type=media_type, filename=os.path.basename(media_path))


@router.get("/jobs/{job_id}", response_model=AutomationJobResponse)
def get_automation_job(job_id: str, db: Session = Depends(get_db)):
    """获取自动化任务进度"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return _job_to_response(job, db)


@router.delete("/jobs/{job_id}")
def delete_automation_job(job_id: str, db: Session = Depends(get_db)):
    """删除素材库或历史记录中的一键流程记录，不删除实际视频文件"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="执行中任务不能删除，请先暂停或取消")
    clear_job_control_requests(db, job)
    _delete_job_record(db, job)
    return {"message": "记录已删除", "job_id": job_id}


@router.post("/jobs/{job_id}/retry", response_model=AutomationStartResponse)
def retry_automation_job(job_id: str, db: Session = Depends(get_db)):
    """重试失败的一键自动化任务"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status not in {"failed", CANCELLED_STATUS, "completed"}:
        raise HTTPException(status_code=400, detail="只有失败、已取消或已完成的自动化任务可以重试")
    if not job.params:
        raise HTTPException(status_code=400, detail="任务缺少原始参数，无法重试")

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    clear_job_control_requests(db, job)
    _reset_job_for_retry(job)
    db.commit()
    _submit_automation_job(job_id)
    return AutomationStartResponse(message="自动化任务已重新进入队列", job_id=job_id)


@router.post("/jobs/{job_id}/resume", response_model=AutomationStartResponse)
def resume_automation_job(job_id: str, db: Session = Depends(get_db)):
    """从已完成阶段后继续执行一键自动化任务"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status not in {"paused", "failed", CANCELLED_STATUS, "completed"}:
        raise HTTPException(status_code=400, detail="只有暂停、失败、已取消或已完成的自动化任务可以继续处理")
    if not job.params:
        raise HTTPException(status_code=400, detail="任务缺少原始参数，无法继续处理")

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    clear_job_control_requests(db, job)
    _prepare_job_for_resume(job)
    db.commit()
    _submit_automation_job(job_id, True)
    return AutomationStartResponse(message="自动化任务已从断点继续", job_id=job_id)


@router.post("/jobs/{job_id}/re-export", response_model=AutomationReExportResponse)
def reexport_automation_job(job_id: str, request: AutomationReExportRequest, db: Session = Depends(get_db)):
    """基于已有任务的阶段产物重新合成导出，供字幕调整页复用"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="任务仍在执行中，请稍后再重新导出")

    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source_video_path = _existing_file(request.video_path or _find_job_source_video_path(job), MEDIA_EXTENSIONS)
    if not source_video_path:
        raise HTTPException(status_code=400, detail="没有可用的源视频，无法重新导出")

    subtitle_path = _existing_file(request.subtitle_path or _find_job_editable_subtitle_path(job, db), EDITABLE_SUBTITLE_EXTENSIONS)
    audio_path = _existing_file(request.audio_path or _find_job_voice_asset_path(job), MEDIA_EXTENSIONS)
    if request.subtitle_path and not subtitle_path:
        raise HTTPException(status_code=404, detail="指定的字幕文件不存在或格式不支持")
    if request.audio_path and not audio_path:
        raise HTTPException(status_code=404, detail="指定的配音文件不存在或格式不支持")
    job_params = _get_job_params(job)
    output_format = str(request.output_format or job_params.get("output_format") or "mp4").strip().lower() or "mp4"
    audio_mode = str(request.audio_mode or job_params.get("audio_mode") or "mix").strip() or "mix"
    original_volume = float(request.original_volume if request.original_volume is not None else job_params.get("original_volume") or 0.25)
    export_with_settings = request.export_with_settings if request.export_with_settings is not None else bool(job_params.get("export_with_settings", True))
    export_settings = request.export_settings or (job_params.get("export_settings") if isinstance(job_params.get("export_settings"), dict) else {})
    final_export_preset = build_final_export_preset(export_settings)
    subtitle_preset_dict = _subtitle_preset_dict_for_export(db, job_params)

    if request.subtitle_path:
        job_params["manual_subtitle_asset_path"] = subtitle_path
        _set_job_params(job, job_params)

    _prepare_job_export_stage_for_rerun(job)
    db.commit()
    workspace_paths = _job_workspace_paths(job, db) or (detect_video_workspace(source_video_path) if source_video_path else None) or ensure_project_dirs()

    export_task = _create_task(
        db,
        job.video_id or 0,
        "export",
        {
            "re_export": True,
            "video_path": source_video_path,
            "subtitle_path": subtitle_path,
            "audio_path": audio_path,
            "output_format": output_format,
            "audio_mode": audio_mode,
            "original_volume": original_volume,
        },
        job.id,
    )
    _update_job_stage(db, job, "export", "running", progress=10, task_id=export_task.id)

    processor = FFmpegProcessor()
    try:
        _check_control(db, job, export_task)
        working_video = source_video_path

        if subtitle_path:
            subtitle_for_burn = _prepare_subtitle_for_burn(
                subtitle_path=subtitle_path,
                output_dir=workspace_paths["output_dir"],
                preset=subtitle_preset_dict,
                suffix="manual_clean",
            )
            working_video = processor.burn_subtitles(
                video_path=working_video,
                subtitle_path=subtitle_for_burn,
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(db, job, export_task, min(55.0, 10.0 + progress * 0.45)),
            )
            _check_control(db, job, export_task)
            export_task.progress = 35
            db.commit()
            _update_job_stage(db, job, "export", "running", progress=35, task_id=export_task.id)

        if audio_path:
            working_video = processor.merge_audio_video(
                video_path=working_video,
                audio_path=audio_path,
                mode=audio_mode,
                volume_ratio=original_volume,
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(db, job, export_task, min(80.0, 35.0 + progress * 0.45)),
            )
            _check_control(db, job, export_task)
            export_task.progress = 70
            db.commit()
            _update_job_stage(db, job, "export", "running", progress=70, task_id=export_task.id)

        if should_apply_final_export_settings(export_with_settings, export_settings):
            render_start = max(15.0, float(export_task.progress or 15.0))
            working_video = processor.apply_effects(
                video_path=working_video,
                preset=final_export_preset,
                output_path=os.path.join(
                    workspace_paths["output_dir"],
                    f"{os.path.splitext(os.path.basename(working_video))[0]}_manual_final.mp4",
                ),
                control_keys=_control_keys(job, export_task),
                progress_callback=lambda progress: _update_export_progress(
                    db,
                    job,
                    export_task,
                    min(92.0, render_start + progress * max(0.0, 92.0 - render_start) / 100.0),
                ),
            )
            _check_control(db, job, export_task)
            export_task.progress = 92
            db.commit()
            _update_job_stage(db, job, "export", "running", progress=92, task_id=export_task.id)

        output_path = processor.convert_format(
            input_path=working_video,
            output_format=output_format,
            control_keys=_control_keys(job, export_task),
            progress_callback=lambda progress: _update_export_progress(
                db,
                job,
                export_task,
                min(95.0, max(80.0, float(export_task.progress or 80.0)) + progress * 0.15),
            ),
        )
        _check_control(db, job, export_task)
        _complete_task(db, export_task, output_path)
        _update_job_stage(db, job, "export", "completed", task_id=export_task.id, output_path=output_path)
        clear_job_control_requests(db, job)
        job.output_path = output_path
        job.status = "completed"
        job.current_step = "重新导出完成"
        job.completed_at = datetime.now()
        job.progress = 100
        db.commit()
        return AutomationReExportResponse(
            message="重新合成导出完成",
            job_id=job.id,
            task_id=export_task.id,
            output_path=output_path,
            subtitle_path=subtitle_path,
            audio_path=audio_path,
            video_path=source_video_path,
        )
    except TaskControlRequested as exc:
        _handle_task_control(db, export_task, exc)
        if exc.action == "pause":
            _pause_running_job(db, job)
        else:
            _cancel_job(db, job)
        raise HTTPException(status_code=409, detail="重新导出已被用户中断") from exc
    except HTTPException:
        raise
    except Exception as exc:
        _fail_task(db, export_task, exc)
        job.status = "failed"
        job.current_step = "重新导出失败"
        job.error_message = str(exc)
        job.completed_at = datetime.now()
        _update_job_stage(db, job, "export", "failed", task_id=export_task.id, error_message=str(exc))
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/skip-current-stage", response_model=AutomationStartResponse)
def skip_current_stage(job_id: str, db: Session = Depends(get_db)):
    """跳过当前自动化阶段，当前支持画面处理阶段"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status != "running":
        raise HTTPException(status_code=400, detail="只有执行中的任务可以跳过当前阶段")

    killed_count = _skip_current_effects_stage(db, job)
    return AutomationStartResponse(message=f"已跳过画面处理并停止 {killed_count} 个运行进程", job_id=job_id)


@router.post("/jobs/{job_id}/pause", response_model=AutomationStartResponse)
def pause_automation_job(job_id: str, db: Session = Depends(get_db)):
    """暂停单个一键自动化任务，并终止当前正在跑的外部进程"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status not in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="只有等待中或执行中的自动化任务可以暂停")
    killed_count = request_job_control(db, job, "pause")
    _pause_running_job(db, job)
    return AutomationStartResponse(message=f"自动化任务已暂停，已停止 {killed_count} 个运行进程", job_id=job_id)


@router.post("/jobs/{job_id}/cancel", response_model=AutomationStartResponse)
def cancel_automation_job(job_id: str, db: Session = Depends(get_db)):
    """取消单个一键自动化任务，并终止当前正在跑的外部进程"""
    job = db.query(AutomationJobRecord).filter(AutomationJobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    if job.status in TERMINAL_STATUSES or job.status == "completed":
        raise HTTPException(status_code=400, detail="任务已经结束，不能重复取消")
    killed_count = request_job_control(db, job, "cancel")
    _cancel_job(db, job)
    return AutomationStartResponse(message=f"自动化任务已取消，已停止 {killed_count} 个运行进程", job_id=job_id)


@router.post("/batch/{batch_id}/pause", response_model=AutomationBatchControlResponse)
def pause_batch_automation(batch_id: str, db: Session = Depends(get_db)):
    """暂停一个批次中尚未开始的自动化任务"""
    with BATCH_SEMAPHORE_LOCK:
        BATCH_PAUSED.add(batch_id)
    affected_count = _pause_batch_jobs(db, batch_id)
    if affected_count == 0:
        raise HTTPException(status_code=404, detail="没有找到可暂停的批量任务")
    return AutomationBatchControlResponse(message="批量任务已暂停", batch_id=batch_id, affected_count=affected_count)


@router.post("/batch/{batch_id}/resume", response_model=AutomationBatchControlResponse)
def resume_batch_automation(batch_id: str, db: Session = Depends(get_db)):
    """恢复一个批次中暂停的自动化任务"""
    try:
        assert_required_tools_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with BATCH_SEMAPHORE_LOCK:
        BATCH_PAUSED.discard(batch_id)
    job_ids = _resume_batch_jobs(db, batch_id)
    for job_id in job_ids:
        _submit_automation_job(job_id)
    return AutomationBatchControlResponse(message="批量任务已恢复", batch_id=batch_id, affected_count=len(job_ids))


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

                payload = _job_to_response(job, db).model_dump_json()
                if payload != last_payload:
                    yield f"event: job\ndata: {payload}\n\n"
                    last_payload = payload
                if job.status in {"completed", "failed", CANCELLED_STATUS, "paused"}:
                    break
            finally:
                db.close()

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
