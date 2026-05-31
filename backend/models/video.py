# backend/models/video.py
# 视频源数据模型 - 存储 YouTube 视频基本信息

from sqlalchemy import Column, Integer, String, DateTime, func
from .database import Base


class VideoSource(Base):
    """
    视频源模型
    存储从 YouTube 解析的视频元数据
    """
    __tablename__ = "video_sources"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 平台标识（youtube、bilibili 等）
    platform = Column(String(50), nullable=False, index=True)
    # 视频 ID（YouTube video_id）
    video_id = Column(String(100), nullable=False, index=True)
    # 视频 URL
    url = Column(String(500), nullable=False)
    # 视频标题
    title = Column(String(500), nullable=True)
    # 作者/频道名
    author = Column(String(200), nullable=True)
    # 视频时长（秒）
    duration = Column(Integer, nullable=True)
    # 缩略图 URL
    thumbnail_url = Column(String(500), nullable=True)
    # 可用清晰度列表（JSON 字符串）
    formats = Column(String(1000), nullable=True)
    # 可用字幕轨列表（JSON 字符串）
    subtitles = Column(String(1000), nullable=True)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
