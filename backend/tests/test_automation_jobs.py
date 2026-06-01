import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.automation import _default_stages, _job_to_response, _prepare_job_for_resume, _reset_job_for_retry, _stage_output_if_reusable  # noqa: E402
from backend.models import AutomationJobRecord  # noqa: E402


class AutomationJobTests(unittest.TestCase):
    def test_job_response_preserves_stage_progress(self):
        job = AutomationJobRecord(
            id="auto-test",
            source_url="https://youtube.com/watch?v=test",
            title="测试任务",
            status="running",
            progress=32,
            current_step="下载入库",
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


if __name__ == "__main__":
    unittest.main()
