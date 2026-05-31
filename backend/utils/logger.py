# backend/utils/logger.py
# 日志工具 - 配置日志输出，禁止输出 API 密钥等敏感信息

import logging
import re
from typing import Optional


class SensitiveFilter(logging.Filter):
    """敏感信息过滤器 - 禁止在日志中输出 API 密钥"""

    # 敏感信息模式列表
    PATTERNS = [
        # API 密钥模式（sk- 开头的 OpenAI 密钥等）
        re.compile(r"sk-[a-zA-Z0-9]{20,}"),
        # Bearer token
        re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*"),
        # 通用 API 密钥模式
        re.compile(r"(api[_-]?key|apikey|token|secret)['\"]?\s*[:=]\s*['\"][^'\"]{10,}['\"]", re.IGNORECASE),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤包含敏感信息的日志记录"""
        msg = record.getMessage()
        for pattern in self.PATTERNS:
            if pattern.search(msg):
                record.msg = "[敏感信息已过滤]"
                record.args = ()
                break
        return True


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取配置好的日志记录器"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 添加敏感信息过滤器
    if not logger.filters:
        logger.addFilter(SensitiveFilter())

    # 控制台处理器
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# 默认日志记录器
logger = get_logger("youtube-processor")
