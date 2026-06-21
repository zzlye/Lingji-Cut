# backend/utils/logger.py
# 日志工具 - 配置日志输出，禁止输出 API 密钥等敏感信息

import logging
import re
import sys
import threading
from collections import deque
from datetime import datetime, timezone


MAX_LOG_RECORDS = 200
_LOG_LOCK = threading.Lock()
_LOG_SEQUENCE = 0
_LOG_RECORDS: deque[dict[str, str | int]] = deque(maxlen=MAX_LOG_RECORDS)


def _activity_level(levelno: int) -> str:
    """把 Python 日志级别收敛成前端活动日志支持的三种级别"""
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    return "info"


def _record_timestamp(record: logging.LogRecord) -> str:
    """生成前端可直接解析的 UTC 时间"""
    return datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ActivityLogHandler(logging.Handler):
    """把后端日志同步写入内存环形缓冲，供前端活动日志读取"""

    def emit(self, record: logging.LogRecord) -> None:
        global _LOG_SEQUENCE
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        with _LOG_LOCK:
            _LOG_SEQUENCE += 1
            _LOG_RECORDS.append({
                "id": _LOG_SEQUENCE,
                "timestamp": _record_timestamp(record),
                "level": _activity_level(record.levelno),
                "source": record.name,
                "message": message,
            })


def get_recent_logs() -> list[dict[str, str | int]]:
    """返回最近 200 条后端活动日志"""
    with _LOG_LOCK:
        return list(_LOG_RECORDS)


def _has_handler(logger: logging.Logger, handler_type: type[logging.Handler]) -> bool:
    return any(isinstance(handler, handler_type) for handler in logger.handlers)


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
    logger.propagate = False

    # 添加敏感信息过滤器
    if not logger.filters:
        logger.addFilter(SensitiveFilter())

    # 控制台处理器
    if not _has_handler(logger, logging.StreamHandler):
        # 普通 INFO/WARNING 日志走 stdout，避免 Electron 把正常运行日志标成 Python Error。
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # 活动日志处理器：只保留最近 200 条，避免长任务刷爆前端内存
    if not _has_handler(logger, ActivityLogHandler):
        activity_handler = ActivityLogHandler()
        activity_handler.setLevel(level)
        logger.addHandler(activity_handler)

    return logger


# 默认日志记录器
logger = get_logger("lingjian-workshop")
