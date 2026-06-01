# backend/models/automation.py
# 自动化任务数据模型 - 存储一键流程的整体状态和阶段明细

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func

from .database import Base


class AutomationJobRecord(Base):
    """一键自动化任务模型"""

    __tablename__ = "automation_jobs"

    # 自动化任务 ID，使用字符串方便前端直接追踪
    id = Column(String(80), primary_key=True)
    # 关联的视频源 ID，解析前允许为空
    video_id = Column(Integer, nullable=True, index=True)
    # 原始视频链接
    source_url = Column(Text, nullable=False)
    # 视频标题或任务标题
    title = Column(String(500), nullable=True)
    # 总体状态：pending、running、completed、failed
    status = Column(String(50), nullable=False, default="pending")
    # 总体进度 0-100
    progress = Column(Float, default=0.0)
    # 当前执行阶段说明
    current_step = Column(String(100), nullable=True)
    # 最终导出文件路径
    output_path = Column(String(500), nullable=True)
    # 字幕正文摘要，供后续配音或调试复用
    subtitle_text = Column(Text, nullable=True)
    # 阶段状态 JSON
    stages = Column(Text, nullable=True)
    # 原始请求参数 JSON
    params = Column(Text, nullable=True)
    # 错误信息
    error_message = Column(Text, nullable=True)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # 完成时间
    completed_at = Column(DateTime, nullable=True)
