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

from backend.api.tasks import cleanup_interrupted_tasks, clear_tasks, delete_task, get_tasks  # noqa: E402
from backend.models import DownloadTask  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟任务查询的最小过滤行为"""

    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.target_id = None
        self.filter_mode = "list"

    def filter(self, *conditions):
        # SQLAlchemy 表达式在单元测试里不执行，这里按接口意图模拟过滤结果。
        if conditions:
            if self.filter_mode == "single":
                self.tasks = [task for task in self.tasks if task.id == self.target_id]
            elif self.filter_mode == "clear":
                self.tasks = [task for task in self.tasks if task.status not in {"processing", "downloading"}]
            else:
                self.tasks = [
                    task for task in self.tasks
                    if not (task.video_id <= 0 and task.parent_job_id is None and task.status == "failed")
                ]
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self.tasks

    def first(self):
        return self.tasks[0] if self.tasks else None


class FakeDb:
    """测试用数据库会话"""

    def __init__(self, tasks):
        self.tasks = tasks
        self.commit_count = 0

    def query(self, *_):
        return FakeQuery(self.tasks)

    def delete(self, task):
        self.tasks.remove(task)

    def commit(self):
        self.commit_count += 1


class FakeSingleDb(FakeDb):
    """测试用单条查询会话，模拟按任务 ID 删除"""

    def __init__(self, tasks, target_id):
        super().__init__(tasks)
        self.target_id = target_id

    def query(self, *_):
        query = FakeQuery(self.tasks)
        query.target_id = self.target_id
        query.filter_mode = "single"
        return query


class FakeClearDb(FakeDb):
    """测试用批量清理会话，模拟保留执行中的任务"""

    def query(self, *_):
        query = FakeQuery(self.tasks)
        query.filter_mode = "clear"
        return query


class FakeRunningDb(FakeDb):
    """测试用执行中任务查询会话"""

    def query(self, *_):
        query = FakeQuery(self.tasks)
        query.tasks = [task for task in self.tasks if task.status in {"processing", "downloading"}]
        return query


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

    def test_delete_task_removes_finished_record(self):
        """已结束的底层任务可以手动删除"""
        task = DownloadTask(id=3, video_id=8, task_type="download", status="failed", progress=0)
        db = FakeSingleDb([task], 3)

        result = asyncio.run(delete_task(3, db=db))

        self.assertEqual(result["task_id"], 3)
        self.assertEqual(db.tasks, [])
        self.assertEqual(db.commit_count, 1)

    def test_delete_task_force_removes_running_record(self):
        """确认卡住后可以强制删除执行中的底层任务记录"""
        task = DownloadTask(id=4, video_id=8, task_type="effects", status="processing", progress=40)
        db = FakeSingleDb([task], 4)

        result = asyncio.run(delete_task(4, force=True, db=db))

        self.assertEqual(result["task_id"], 4)
        self.assertEqual(db.tasks, [])
        self.assertEqual(db.commit_count, 1)

    def test_clear_tasks_keeps_running_by_default(self):
        """批量清理默认保留执行中的任务"""
        failed = DownloadTask(id=1, video_id=8, task_type="download", status="failed", progress=0)
        completed = DownloadTask(id=2, video_id=8, task_type="download", status="completed", progress=100)
        running = DownloadTask(id=3, video_id=8, task_type="effects", status="processing", progress=35)
        db = FakeClearDb([failed, completed, running])

        result = asyncio.run(clear_tasks(db=db))

        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual([task.id for task in db.tasks], [3])

    def test_cleanup_interrupted_marks_running_as_failed(self):
        """卡住的执行中任务可一键标记失败，随后用户可以删除或重试"""
        running = DownloadTask(id=3, video_id=8, task_type="effects", status="processing", progress=35)
        downloading = DownloadTask(id=4, video_id=8, task_type="download", status="downloading", progress=12)
        completed = DownloadTask(id=5, video_id=8, task_type="export", status="completed", progress=100)
        db = FakeRunningDb([running, downloading, completed])

        result = asyncio.run(cleanup_interrupted_tasks(db=db))

        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(running.status, "failed")
        self.assertEqual(downloading.status, "failed")
        self.assertEqual(completed.status, "completed")
        self.assertEqual(db.commit_count, 1)


if __name__ == "__main__":
    unittest.main()
