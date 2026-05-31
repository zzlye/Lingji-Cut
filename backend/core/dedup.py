# backend/core/dedup.py
# 去重检查器 - 按平台+视频ID和文件哈希去重

import json
import os
import hashlib
from typing import Optional
from sqlalchemy.orm import Session
from ..models import VideoSource, DownloadTask
from ..utils import get_logger

# 日志记录器
logger = get_logger("dedup")


class DedupChecker:
    """去重检查器"""

    def __init__(self, db: Session):
        """初始化去重检查器"""
        self.db = db

    def check_by_video_id(self, platform: str, video_id: str) -> Optional[VideoSource]:
        """
        第一层去重：按平台 + 视频 ID 检查
        返回已存在的视频源，如果不存在返回 None
        """
        existing = self.db.query(VideoSource).filter(
            VideoSource.platform == platform,
            VideoSource.video_id == video_id
        ).first()

        if existing:
            logger.info(f"发现重复视频（按ID）: {platform}/{video_id}")
            return existing

        return None

    def check_by_file_hash(self, file_path: str) -> Optional[DownloadTask]:
        """
        第二层去重：按文件 SHA256 哈希检查
        返回已存在的任务，如果不存在返回 None
        """
        if not os.path.exists(file_path):
            return None

        # 计算文件哈希
        file_hash = self._calculate_hash(file_path)
        logger.info(f"计算文件哈希: {file_hash}")

        # 在数据库中查找相同哈希的任务
        # 注意：这里需要在 output_path 对应的文件上计算哈希
        # 简化实现：遍历已完成的任务
        completed_tasks = self.db.query(DownloadTask).filter(
            DownloadTask.status == "completed",
            DownloadTask.output_path.isnot(None)
        ).all()

        for task in completed_tasks:
            if task.output_path and os.path.exists(task.output_path):
                existing_hash = self._calculate_hash(task.output_path)
                if existing_hash == file_hash:
                    logger.info(f"发现重复文件（按哈希）: {task.output_path}")
                    return task

        return None

    def _calculate_hash(self, file_path: str) -> str:
        """计算文件 SHA256 哈希"""
        sha256_hash = hashlib.sha256()

        with open(file_path, "rb") as f:
            # 分块读取文件（8KB 块）
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)

        return sha256_hash.hexdigest()

    def add_video_source(self, video_info: dict) -> VideoSource:
        """添加新的视频源记录"""
        video_source = VideoSource(
            platform=video_info["platform"],
            video_id=video_info["video_id"],
            url=video_info["url"],
            title=video_info.get("title"),
            author=video_info.get("author"),
            duration=video_info.get("duration"),
            thumbnail_url=video_info.get("thumbnail_url"),
            formats=json.dumps(video_info.get("formats", []), ensure_ascii=False),
            subtitles=json.dumps(video_info.get("subtitles", []), ensure_ascii=False),
        )

        self.db.add(video_source)
        self.db.commit()
        self.db.refresh(video_source)

        logger.info(f"添加视频源: {video_source.title}")
        return video_source
