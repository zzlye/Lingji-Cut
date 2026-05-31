# backend/models/task.py
# 任务数据模型 - 存储下载、处理、导出任务状态

from sqlalchemy import Column, Integer, String, DateTime, Float, Text, func
from .database import Base


class DownloadTask(Base):
    """
    下载任务模型
    存储视频下载和处理任务的状态信息
    """
    __tablename__ = "download_tasks"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 关联的视频源 ID
    video_id = Column(Integer, nullable=False, index=True)
    # 任务类型（download、subtitle、voice、export）
    task_type = Column(String(50), nullable=False)
    # 任务状态（pending、downloading、processing、completed、failed）
    status = Column(String(50), nullable=False, default="pending")
    # 下载进度（0-100）
    progress = Column(Float, default=0.0)
    # 下载速度（bytes/s）
    speed = Column(Float, nullable=True)
    # 输出文件路径
    output_path = Column(String(500), nullable=True)
    # 错误信息
    error_message = Column(Text, nullable=True)
    # 任务参数（JSON 字符串）
    params = Column(Text, nullable=True)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # 完成时间
    completed_at = Column(DateTime, nullable=True)
