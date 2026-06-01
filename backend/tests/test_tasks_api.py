# backend/tests/test_tasks_api.py
# 任务列表接口测试 - 验证孤儿失败任务不会污染用户任务视图

import asyncio
import os
import sys
import unittest


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.tasks import get_tasks  # noqa: E402
from backend.models import DownloadTask  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟任务查询的最小过滤行为"""

    def __init__(self, tasks):
        self.tasks = list(tasks)

    def filter(self, *conditions):
        # SQLAlchemy 表达式在单元测试里不执行，这里按接口意图模拟过滤结果。
        if conditions:
            self.tasks = [
                task for task in self.tasks
                if not (task.video_id <= 0 and task.parent_job_id is None and task.status == "failed")
            ]
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self.tasks


class FakeDb:
    """测试用数据库会话"""

    def __init__(self, tasks):
        self.tasks = tasks

    def query(self, *_):
        return FakeQuery(self.tasks)


class TasksApiTests(unittest.TestCase):
    """任务列表接口测试"""

    def test_get_tasks_hides_orphan_failed_records_by_default(self):
        """默认任务列表不展示 video_id=0 且没有自动化归属的失败任务"""
        orphan = DownloadTask(id=1, video_id=0, task_type="export", status="failed", progress=0, error_message="输入文件不存在: D:\\missing.mp4")
        real_task = DownloadTask(id=2, video_id=8, task_type="download", status="completed", progress=100)

        result = asyncio.run(get_tasks(db=FakeDb([orphan, real_task])))

        self.assertEqual([task.id for task in result], [2])

    def test_get_tasks_can_include_orphan_records_for_debugging(self):
        """调试时可以显式读取孤儿失败任务"""
        orphan = DownloadTask(id=1, video_id=0, task_type="export", status="failed", progress=0, error_message="输入文件不存在: D:\\missing.mp4")
        real_task = DownloadTask(id=2, video_id=8, task_type="download", status="completed", progress=100)

        result = asyncio.run(get_tasks(include_orphans=True, db=FakeDb([orphan, real_task])))

        self.assertEqual([task.id for task in result], [1, 2])


if __name__ == "__main__":
    unittest.main()
