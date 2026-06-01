# backend/core/task_runtime.py
# 任务运行时清理 - 根据任务记录和文件路径兜底终止外部处理进程

import json
import os
from typing import Any

from sqlalchemy.orm import Session

from .process_control import request_control, terminate_matching_tool_processes
from .paths import ensure_project_dirs
from ..models import AutomationJobRecord, DownloadTask, VideoSource


def request_task_control(db: Session, task: DownloadTask, action: str) -> int:
    """暂停或取消底层任务，同时按任务文件路径兜底清理旧进程"""
    killed_count = request_control(f"task:{task.id}", action)
    killed_count += terminate_task_external_processes(db, task)
    return killed_count


def request_job_control(db: Session, job: AutomationJobRecord, action: str) -> int:
    """暂停或取消自动化任务，同时清理该任务关联的所有外部工具进程"""
    killed_count = request_control(f"job:{job.id}", action)
    for task in _tasks_for_job(db, job):
        killed_count += request_control(f"task:{task.id}", action)
        killed_count += terminate_task_external_processes(db, task)
    killed_count += terminate_matching_tool_processes(_job_match_fragments(db, job))
    return killed_count


def terminate_task_external_processes(db: Session, task: DownloadTask) -> int:
    """根据任务参数、输出路径和视频标题匹配 ffmpeg/yt-dlp 命令行并终止"""
    return terminate_matching_tool_processes(_task_match_fragments(db, task))


def mark_job_child_tasks_controlled(db: Session, job: AutomationJobRecord, status: str, message: str) -> int:
    """把自动化任务下仍在执行的底层任务同步成暂停或取消"""
    updated = 0
    for task in _tasks_for_job(db, job):
        active_statuses = {"processing", "downloading"} if status == "skipped" else {"pending", "processing", "downloading", "paused"}
        if task.status not in active_statuses:
            continue
        task.status = status
        task.error_message = message
        updated += 1
    return updated


def _tasks_for_job(db: Session, job: AutomationJobRecord) -> list[DownloadTask]:
    """读取自动化任务关联的底层任务，包含阶段里记录过的 task_id"""
    task_ids: set[int] = set()
    try:
        stages = json.loads(job.stages or "[]")
    except json.JSONDecodeError:
        stages = []
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and stage.get("task_id"):
                try:
                    task_ids.add(int(stage["task_id"]))
                except (TypeError, ValueError):
                    pass

    try:
        query = db.query(DownloadTask).filter(DownloadTask.parent_job_id == job.id)
        tasks = list(query.all())
    except Exception:
        # 单元测试里的 FakeDb 只模拟自动化任务查询，没有底层任务表。
        tasks = []
    if task_ids:
        try:
            found_ids = {task.id for task in tasks}
            extra_tasks = db.query(DownloadTask).filter(DownloadTask.id.in_(task_ids - found_ids)).all()
            tasks.extend(extra_tasks)
        except Exception:
            pass
    return [task for task in tasks if isinstance(task, DownloadTask)]


def _task_match_fragments(db: Session, task: DownloadTask) -> list[str]:
    """生成能够定位任务外部进程的命令行片段"""
    fragments: list[str] = []
    params = _safe_json(task.params)
    for key in ("video_path", "input_path", "output_path", "subtitle_path", "audio_path"):
        value = params.get(key)
        if isinstance(value, str):
            fragments.append(value)
    if task.output_path:
        fragments.append(task.output_path)

    video = _video_for_task(db, task)
    if video:
        fragments.extend(_video_fragments(video))
        fragments.extend(_known_stage_outputs(db, video.id, task.parent_job_id))

    # 自动化画面处理旧任务有时没有把 downloaded_path 写入 params，用视频标题和默认目录兜底匹配。
    if task.task_type in {"effects", "subtitle", "export"} and video:
        paths = ensure_project_dirs()
        title = _safe_file_name(video.title or video.video_id)
        if title:
            fragments.extend([
                os.path.join(paths["downloads_dir"], f"{title}.mp4"),
                os.path.join(paths["output_dir"], f"{title}_enhanced.mp4"),
                os.path.join(paths["output_dir"], f"{title}_subtitled.mp4"),
                os.path.join(paths["exports_dir"], f"{title}_enhanced.mp4"),
            ])
    return _dedupe_fragments(fragments)


def _job_match_fragments(db: Session, job: AutomationJobRecord) -> list[str]:
    """生成自动化任务级别的命令行匹配片段"""
    fragments: list[str] = []
    if job.output_path:
        fragments.append(job.output_path)
    video = _video_for_job(db, job)
    if video:
        fragments.extend(_video_fragments(video))
        fragments.extend(_known_stage_outputs(db, video.id, job.id))
    return _dedupe_fragments(fragments)


def _video_fragments(video: VideoSource) -> list[str]:
    """从视频记录生成 URL、标题和视频 ID 匹配片段"""
    return [value for value in [video.url, video.video_id, video.title] if value]


def _known_stage_outputs(db: Session, video_id: int, parent_job_id: str | None = None) -> list[str]:
    """根据已完成任务输出路径补充匹配片段"""
    try:
        query = db.query(DownloadTask).filter(DownloadTask.video_id == video_id)
        if parent_job_id:
            query = query.filter(DownloadTask.parent_job_id == parent_job_id)
        return [task.output_path for task in query.all() if task.output_path]
    except Exception:
        return []


def _video_for_task(db: Session, task: DownloadTask) -> VideoSource | None:
    """读取任务关联视频，测试假对象返回其他类型时直接忽略"""
    if not task.video_id:
        return None
    try:
        video = db.query(VideoSource).filter(VideoSource.id == task.video_id).first()
    except Exception:
        return None
    return video if isinstance(video, VideoSource) else None


def _video_for_job(db: Session, job: AutomationJobRecord) -> VideoSource | None:
    """读取自动化任务关联视频"""
    if not job.video_id:
        return None
    try:
        video = db.query(VideoSource).filter(VideoSource.id == job.video_id).first()
    except Exception:
        return None
    return video if isinstance(video, VideoSource) else None


def _safe_json(value: str | None) -> dict[str, Any]:
    """安全解析任务参数 JSON"""
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _safe_file_name(value: str) -> str:
    """保留 yt-dlp 常见标题文件名，去掉 Windows 不允许的字符"""
    text = str(value or "").strip()
    for char in '<>:"/\\|?*':
        text = text.replace(char, "")
    return text.strip()


def _dedupe_fragments(fragments: list[str]) -> list[str]:
    """去重并过滤过短片段，避免误杀无关进程"""
    result: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        normalized = str(fragment or "").strip()
        if len(normalized) < 8 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
