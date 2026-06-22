# backend/tests/test_activity_logs.py
# 活动日志回归测试 - 确认后端业务日志能进入最近 200 条缓冲

import unittest
import logging
import os
import sys

# 嵌入式 Python 直接运行测试时需要手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.utils import get_logger, get_recent_logs


class ActivityLogsTest(unittest.TestCase):
    def test_backend_logs_keep_latest_200_records(self):
        logger = get_logger("test_activity_logs")
        stream_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.StreamHandler)]
        old_levels = [handler.level for handler in stream_handlers]

        try:
            for handler in stream_handlers:
                handler.setLevel(logging.CRITICAL + 1)
            for index in range(205):
                logger.info(f"activity-buffer-{index}")
        finally:
            for handler, level in zip(stream_handlers, old_levels):
                handler.setLevel(level)

        logs = get_recent_logs()
        messages = [str(item["message"]) for item in logs]

        self.assertLessEqual(len(logs), 200)
        self.assertIn("activity-buffer-204", messages)
        self.assertNotIn("activity-buffer-0", messages)

    def test_activity_log_can_use_short_summary(self):
        """活动抽屉可使用短摘要，避免 traceback 等长日志撑破前端布局"""
        logger = get_logger("test_activity_logs_summary")
        logger.warning(
            "详细错误第一行\nTraceback 很长很长",
            extra={"activity_message": "短摘要：已回退 CPU"},
        )

        logs = get_recent_logs()
        self.assertEqual(logs[-1]["message"], "短摘要：已回退 CPU")


if __name__ == "__main__":
    unittest.main()
