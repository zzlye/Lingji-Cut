# backend/models/processing.py
# 画面处理预设模型 - 保存视频增强/差异化处理模板

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func

from .database import Base


class ProcessingPreset(Base):
    """
    画面处理预设模型
    使用 JSON 字符串保存完整参数，便于后续扩展滤镜能力。
    """
    __tablename__ = "processing_presets"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 预设名称
    name = Column(String(100), nullable=False)
    # 预设强度（light、standard、strong、custom）
    intensity = Column(String(50), nullable=False, default="standard")
    # 是否为默认预设
    is_default = Column(Boolean, default=False)
    # 画面处理配置 JSON
    config_json = Column(Text, nullable=False)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
