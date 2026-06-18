# backend/tests/test_activity_logs.py
# 活动日志回归测试 - 确认后端业务日志能进入最近 200 条缓冲

import unittest
import logging

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


if __name__ == "__main__":
    unittest.main()
