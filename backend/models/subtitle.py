# backend/models/subtitle.py
# 字幕预设数据模型 - 存储字幕样式配置

from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from .database import Base


class SubtitlePreset(Base):
    """
    字幕预设模型
    存储字幕样式配置（字体、颜色、位置等）
    """
    __tablename__ = "subtitle_presets"

    # 主键 ID
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 预设名称
    name = Column(String(100), nullable=False)
    # 是否为默认预设
    is_default = Column(Boolean, default=False)
    # 单行/双行模式（single、double）
    line_mode = Column(String(20), default="single")
    # 字幕语言代码或自定义语言名称
    language = Column(String(50), default="auto")
    # 字体名称
    font_name = Column(String(100), default="Microsoft YaHei")
    # 字体大小
    font_size = Column(Integer, default=48)
    # 字体颜色（十六进制）
    font_color = Column(String(20), default="#FFFFFF")
    # 双行字幕第二行或强调字幕颜色
    secondary_color = Column(String(20), default="#FDE68A")
    # 描边颜色
    outline_color = Column(String(20), default="#000000")
    # 描边宽度
    outline_width = Column(Integer, default=2)
    # 是否启用阴影
    shadow_enabled = Column(Boolean, default=True)
    # 阴影颜色
    shadow_color = Column(String(20), default="#80000000")
    # 阴影偏移 X
    shadow_x = Column(Integer, default=2)
    # 阴影偏移 Y
    shadow_y = Column(Integer, default=2)
    # 背景透明度（0-255）
    background_alpha = Column(Integer, default=0)
    # 位置（九宫格：top_left、top、top_right、middle_left、center、middle_right、bottom_left、bottom、bottom_right）
    position = Column(String(20), default="bottom")
    # 边距
    margin_v = Column(Integer, default=30)
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
