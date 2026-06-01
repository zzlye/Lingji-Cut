import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.automation import _create_automation_job, _default_stages, _get_batch_concurrency_from_job, _is_batch_paused, _job_to_response, _normalize_batch_urls, _prepare_interrupted_job_for_startup, _restore_batch_runtime_state, _pause_batch_jobs, _prepare_job_for_resume, _register_batch_pause, _resume_batch_jobs, _reset_job_for_retry, _stage_output_if_reusable, AutomationRunRequest, BATCH_PAUSED, BATCH_SEMAPHORES, subtitle_entries_to_voice_segments  # noqa: E402
from backend.models import AutomationJobRecord  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟 SQLAlchemy 的最小行为"""

    def __init__(self, jobs):
        self.jobs = jobs

    def order_by(self, *_):
        return self

    def all(self):
        return self.jobs

    def first(self):
        return self.jobs[0] if self.jobs else None


class FakeDb:
    """测试用数据库会话，避免污染本地 SQLite"""

    def __init__(self, jobs):
        self.jobs = jobs
        self.commit_count = 0

    def query(self, *_):
        return FakeQuery(self.jobs)

    def commit(self):
        self.commit_count += 1

    def add(self, job):
        self.jobs.append(job)


class AutomationJobTests(unittest.TestCase):
    def tearDown(self):
        """清理批次运行时状态，避免测试互相影响"""
        BATCH_PAUSED.clear()
        BATCH_SEMAPHORES.clear()

    def test_job_response_preserves_stage_progress(self):
        job = AutomationJobRecord(
            id="auto-test",
            source_url="https://youtube.com/watch?v=test",
            title="测试任务",
            status="running",
            progress=32,
            current_step="下载入库",
            params=json.dumps({"batch_id": "batch-test"}, ensure_ascii=False),
            stages=json.dumps([
                {"key": "parse", "status": "completed", "progress": 100, "task_id": None, "output_path": None, "error_message": None},
                {"key": "download", "status": "running", "progress": 42, "task_id": 7, "output_path": None, "error_message": None},
            ], ensure_ascii=False),
        )

        response = _job_to_response(job)

        self.assertEqual(response.id, "auto-test")
        self.assertEqual(response.stages[0].status, "completed")
        self.assertEqual(response.stages[1].progress, 42)
        self.assertEqual(response.stages[1].task_id, 7)
        self.assertEqual(response.batch_id, "batch-test")

    def test_default_stages_include_full_automation_flow(self):
        keys = [stage["key"] for stage in _default_stages()]

        self.assertEqual(keys, ["parse", "download", "effects", "subtitle", "voice", "export"])

    def test_retry_reset_clears_previous_runtime_state(self):
        job = AutomationJobRecord(
            id="auto-retry",
            source_url="https://youtube.com/watch?v=test",
            title="测试任务",
            status="failed",
            progress=64,
            current_step="流程失败",
            output_path="D:/old.mp4",
            subtitle_text="旧字幕",
            error_message="旧错误",
            completed_at=None,
            stages=json.dumps([
                {"key": "parse", "status": "completed", "progress": 100, "task_id": None, "output_path": None, "error_message": None},
            ], ensure_ascii=False),
        )

        _reset_job_for_retry(job)

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.progress, 0)
        self.assertEqual(job.current_step, "等待重试")
        self.assertIsNone(job.output_path)
        self.assertIsNone(job.subtitle_text)
        self.assertIsNone(job.error_message)
        self.assertEqual([stage["key"] for stage in json.loads(job.stages)], ["parse", "download", "effects", "subtitle", "voice", "export"])

    def test_resume_keeps_completed_stages_and_clears_failed_stage(self):
        job = AutomationJobRecord(
            id="auto-resume",
            source_url="https://youtube.com/watch?v=test",
            status="failed",
            progress=70,
            current_step="流程失败",
            error_message="导出失败",
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
                {"key": "effects", "status": "completed", "progress": 100, "task_id": 2, "output_path": "D:/effects.mp4", "error_message": None},
                {"key": "export", "status": "failed", "progress": 35, "task_id": 3, "output_path": None, "error_message": "导出失败"},
            ], ensure_ascii=False),
        )

        _prepare_job_for_resume(job)
        stages = {stage["key"]: stage for stage in json.loads(job.stages)}

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.current_step, "等待继续")
        self.assertIsNone(job.error_message)
        self.assertEqual(stages["download"]["status"], "completed")
        self.assertEqual(stages["effects"]["status"], "completed")
        self.assertEqual(stages["export"]["status"], "pending")
        self.assertIsNone(stages["export"]["task_id"])
        self.assertIsNone(stages["export"]["error_message"])

    def test_prepare_interrupted_job_for_startup_clears_running_stage(self):
        job = AutomationJobRecord(
            id="auto-startup",
            source_url="https://youtube.com/watch?v=test",
            status="running",
            progress=50,
            current_step="字幕处理",
            error_message="旧错误",
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
                {"key": "subtitle", "status": "running", "progress": 55, "task_id": 2, "output_path": None, "error_message": "中断"},
            ], ensure_ascii=False),
        )

        _prepare_interrupted_job_for_startup(job)
        stages = {stage["key"]: stage for stage in json.loads(job.stages)}

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.current_step, "后端重启后等待恢复")
        self.assertIsNone(job.error_message)
        self.assertEqual(stages["download"]["status"], "completed")
        self.assertEqual(stages["subtitle"]["status"], "pending")
        self.assertIsNone(stages["subtitle"]["task_id"])
        self.assertIsNone(stages["subtitle"]["error_message"])

    def test_stage_output_reusable_requires_existing_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
            temp_path = temp_file.name
        try:
            job = AutomationJobRecord(
                id="auto-stage-file",
                source_url="https://youtube.com/watch?v=test",
                status="failed",
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": temp_path, "error_message": None},
                    {"key": "effects", "status": "completed", "progress": 100, "task_id": 2, "output_path": "D:/missing.mp4", "error_message": None},
                ], ensure_ascii=False),
            )

            self.assertEqual(_stage_output_if_reusable(job, "download"), temp_path)
            self.assertIsNone(_stage_output_if_reusable(job, "effects"))
        finally:
            os.remove(temp_path)

    def test_batch_urls_are_trimmed_and_deduplicated(self):
        urls = _normalize_batch_urls([
            "  https://youtube.com/watch?v=1  ",
            "",
            "https://youtube.com/watch?v=2",
            "https://youtube.com/watch?v=1",
            "   ",
        ])

        self.assertEqual(urls, [
            "https://youtube.com/watch?v=1",
            "https://youtube.com/watch?v=2",
        ])

    def test_batch_pause_and_resume_update_pending_jobs(self):
        jobs = [
            AutomationJobRecord(id="auto-1", source_url="https://youtube.com/1", status="pending", params=json.dumps({"batch_id": "batch-a"})),
            AutomationJobRecord(id="auto-2", source_url="https://youtube.com/2", status="running", params=json.dumps({"batch_id": "batch-a"})),
            AutomationJobRecord(id="auto-3", source_url="https://youtube.com/3", status="completed", params=json.dumps({"batch_id": "batch-a"})),
            AutomationJobRecord(id="auto-4", source_url="https://youtube.com/4", status="pending", params=json.dumps({"batch_id": "batch-b"})),
        ]
        db = FakeDb(jobs)

        paused_count = _pause_batch_jobs(db, "batch-a")

        self.assertEqual(paused_count, 2)
        self.assertEqual(jobs[0].status, "paused")
        self.assertEqual(jobs[0].current_step, "批次暂停")
        self.assertEqual(jobs[1].status, "running")
        self.assertTrue(json.loads(jobs[0].params)["batch_paused"])
        self.assertTrue(json.loads(jobs[1].params)["batch_paused"])
        self.assertEqual(jobs[3].status, "pending")

        resumed_ids = _resume_batch_jobs(db, "batch-a")

        self.assertEqual(resumed_ids, ["auto-1"])
        self.assertEqual(jobs[0].status, "pending")
        self.assertEqual(jobs[0].current_step, "等待批次调度")
        self.assertFalse(json.loads(jobs[0].params)["batch_paused"])

    def test_create_batch_job_stores_concurrency_for_resume_after_restart(self):
        db = FakeDb([])

        job = _create_automation_job(
            db,
            AutomationRunRequest(url="https://youtube.com/watch?v=test"),
            batch_id="batch-a",
            batch_concurrency=6,
        )

        params = json.loads(job.params)
        self.assertEqual(params["batch_id"], "batch-a")
        self.assertEqual(params["batch_concurrency"], 6)
        self.assertEqual(_get_batch_concurrency_from_job(job), 6)

    def test_restore_batch_runtime_state_keeps_paused_batches(self):
        jobs = [
            AutomationJobRecord(id="auto-paused", source_url="https://youtube.com/1", status="paused", params=json.dumps({"batch_id": "batch-a", "batch_concurrency": 5, "batch_paused": True})),
            AutomationJobRecord(id="auto-pending", source_url="https://youtube.com/2", status="pending", params=json.dumps({"batch_id": "batch-b", "batch_concurrency": 3})),
        ]

        _restore_batch_runtime_state(jobs)

        self.assertTrue(_is_batch_paused("batch-a"))
        self.assertFalse(_is_batch_paused("batch-b"))
        self.assertIn("batch-a", BATCH_SEMAPHORES)
        self.assertIn("batch-b", BATCH_SEMAPHORES)

    def test_register_batch_pause_sets_runtime_pause_flag(self):
        _register_batch_pause("batch-manual")

        self.assertTrue(_is_batch_paused("batch-manual"))

    def test_subtitle_entries_to_voice_segments_preserves_timeline(self):
        entries = [
            {"index": 1, "start": "00:00:01,250", "end": "00:00:03,000", "text": "第一句字幕"},
            {"index": 2, "start": "00:00:03,000", "end": "00:00:05,000", "text": "第二句字幕" * 80},
        ]

        segments = subtitle_entries_to_voice_segments(entries, max_chars_per_segment=30)

        self.assertGreater(len(segments), 2)
        self.assertEqual(segments[0]["start_ms"], 1250)
        self.assertEqual(segments[0]["end_ms"], 3000)
        self.assertTrue(all(segment["text"] for segment in segments))
        self.assertEqual(segments[-1]["end_ms"], 5000)


if __name__ == "__main__":
    unittest.main()
