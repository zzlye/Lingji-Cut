import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.automation import _apply_glossary_terms, _build_subtitle_download_candidates, _cancel_job, _create_automation_job, _default_stages, _delete_job_record, _download_subtitle_with_fallback, _find_banned_words, _get_batch_concurrency_from_job, _is_batch_paused, _job_to_response, _normalize_batch_urls, _pause_running_job, _pick_text_profile, _prepare_interrupted_job_for_startup, _restore_batch_runtime_state, _pause_batch_jobs, _prepare_job_for_resume, _register_batch_pause, _resume_batch_jobs, _reset_job_for_retry, _skip_current_effects_stage, _stage_output_if_reusable, _voice_for_segment, build_final_export_preset, combine_original_and_translated_entries, merge_subtitle_burn_preset, should_apply_final_export_settings, AutomationRunRequest, BATCH_PAUSED, BATCH_SEMAPHORES, subtitle_entries_to_voice_segments  # noqa: E402
from backend.api.automation import _run_automation_sync  # noqa: E402
from backend.models import AutomationJobRecord, DownloadTask, TextProviderProfile, VideoSource  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟 SQLAlchemy 的最小行为"""

    def __init__(self, jobs):
        self.jobs = jobs

    def order_by(self, *_):
        return self

    def filter(self, *_):
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

    def delete(self, job):
        if job in self.jobs:
            self.jobs.remove(job)


class FakeTaskDb(FakeDb):
    """测试用数据库会话，支持按模型返回任务或自动化任务"""

    def __init__(self, jobs, tasks):
        super().__init__(jobs)
        self.tasks = tasks

    def query(self, model):
        if model is DownloadTask:
            return FakeQuery(self.tasks)
        return FakeQuery(self.jobs)

    def delete(self, item):
        if item in self.tasks:
            self.tasks.remove(item)
        elif item in self.jobs:
            self.jobs.remove(item)


class FakeSubtitleDownloader:
    """测试用字幕下载器，模拟首选语言被限流后其它语言成功"""

    def __init__(self, success_language: str):
        self.success_language = success_language
        self.calls: list[dict[str, str]] = []

    def download_subtitle(self, url, language, output_dir, sub_type, control_keys):
        """记录下载参数，只有指定语言返回成功"""
        self.calls.append({"url": url, "language": language, "sub_type": sub_type, "control": ",".join(control_keys or [])})
        if language != self.success_language:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return os.path.join(output_dir, f"subtitle.{language}.vtt")


class FakeAutomationDownloader:
    """测试用一键流程下载器，字幕下载一旦被调用就失败"""

    def __init__(self, download_path: str):
        self.download_path = download_path
        self.subtitle_download_calls = 0

    def download_video(self, **_):
        """返回已准备好的本地视频路径"""
        return self.download_path

    def download_subtitle(self, *_args, **_kwargs):
        """一键流程不应再下载字幕"""
        self.subtitle_download_calls += 1
        raise AssertionError("一键流程不应调用字幕下载")


class FakeAutomationProcessor:
    """测试用媒体处理器，避免真正运行 ffmpeg"""

    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.burn_calls: list[dict] = []
        self.effects_calls: list[dict] = []

    def burn_subtitles(self, **kwargs):
        """记录烧录参数并返回假输出文件"""
        self.burn_calls.append(kwargs)
        output_path = os.path.join(self.temp_dir, "subtitled.mp4")
        with open(output_path, "wb") as file:
            file.write(b"subtitled")
        return output_path

    def apply_effects(self, **kwargs):
        """记录最终导出渲染参数并返回假输出文件"""
        self.effects_calls.append(kwargs)
        output_path = os.path.join(self.temp_dir, "final.mp4")
        with open(output_path, "wb") as file:
            file.write(b"final")
        return output_path

    def convert_format(self, **_kwargs):
        """返回假导出文件"""
        output_path = os.path.join(self.temp_dir, "exported.mp4")
        with open(output_path, "wb") as file:
            file.write(b"exported")
        return output_path


class FakeAutomationRecognizer:
    """测试用本地识别器，返回固定字幕"""

    def __init__(self):
        self.video_paths: list[str] = []

    def transcribe_video(self, video_path, progress_callback=None):
        """记录识别输入并模拟识别进度"""
        self.video_paths.append(video_path)
        if progress_callback:
            progress_callback(100)
        return ([{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "本地识别字幕"}], "zh")


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
        self.assertTrue(response.can_pause)
        self.assertTrue(response.can_cancel)

    def test_default_stages_include_full_automation_flow(self):
        keys = [stage["key"] for stage in _default_stages()]

        self.assertEqual(keys, ["parse", "download", "effects", "subtitle", "voice", "export"])

    def test_automation_uses_local_asr_instead_of_subtitle_download(self):
        """一键流程字幕阶段使用本地识别，不再调用字幕下载"""
        with tempfile.TemporaryDirectory(prefix="automation_local_asr_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=1, platform="youtube", video_id="local-asr", url="https://example.test/video", title="测试视频")
            task_ids = iter(range(1, 10))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation.ensure_project_dirs", return_value={"output_dir": temp_dir, "exports_dir": temp_dir}),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        processing_preset={
                            "bitrate": {
                                "enabled": True,
                                "mode": "fixed",
                                "fixed_kbps": {"enabled": True, "random": False, "value": 2200, "min": 2200, "max": 2200},
                            },
                            "acceleration": {"enabled": True, "mode": "auto", "quality": "size"},
                        },
                        enable_voice=False,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(fake_downloader.subtitle_download_calls, 0)
        self.assertEqual(fake_recognizer.video_paths, [downloaded_path])
        self.assertIn("本地识别字幕", response.subtitle_text)
        self.assertEqual(stage_by_key["subtitle"].status, "completed")
        self.assertEqual(fake_processor.burn_calls[0]["video_path"], downloaded_path)
        self.assertEqual(fake_processor.burn_calls[0]["preset"]["bitrate"]["fixed_kbps"]["value"], 2200)
        self.assertEqual(fake_processor.burn_calls[0]["preset"]["acceleration"]["quality"], "size")

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

    def test_resume_keeps_completed_stages_and_clears_failed_paused_cancelled_stage(self):
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
                {"key": "voice", "status": "paused", "progress": 20, "task_id": 4, "output_path": None, "error_message": "暂停"},
                {"key": "subtitle", "status": "cancelled", "progress": 10, "task_id": 5, "output_path": None, "error_message": "取消"},
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
        self.assertEqual(stages["voice"]["status"], "pending")
        self.assertEqual(stages["subtitle"]["status"], "pending")

    def test_pause_and_cancel_job_update_controls_and_stages(self):
        """单任务暂停/取消会影响自动化任务状态和阶段状态"""
        stages = [
            {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
            {"key": "effects", "status": "running", "progress": 40, "task_id": 2, "output_path": None, "error_message": None},
            {"key": "export", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        ]
        job = AutomationJobRecord(id="auto-control", source_url="https://youtube.com/watch?v=test", status="running", stages=json.dumps(stages, ensure_ascii=False))
        db = FakeDb([job])

        _pause_running_job(db, job)
        paused_response = _job_to_response(job)

        self.assertEqual(job.status, "paused")
        self.assertTrue(paused_response.can_resume)
        self.assertTrue(paused_response.can_cancel)
        self.assertEqual(json.loads(job.stages)[1]["status"], "paused")

        _cancel_job(db, job)
        cancelled_response = _job_to_response(job)

        self.assertEqual(job.status, "cancelled")
        self.assertTrue(cancelled_response.can_resume)
        self.assertTrue(cancelled_response.can_retry)
        self.assertIsNotNone(job.completed_at)

    def test_skip_effects_controls_only_current_effects_task(self):
        """跳过画面处理只控制当前阶段任务，不能污染后续字幕和导出"""
        stages = [
            {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
            {"key": "effects", "status": "running", "progress": 50, "task_id": 2, "output_path": None, "error_message": None},
            {"key": "subtitle", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
            {"key": "export", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
        ]
        job = AutomationJobRecord(
            id="auto-skip-effects",
            source_url="https://youtube.com/watch?v=test",
            status="running",
            params=json.dumps({"enable_effects": True}, ensure_ascii=False),
            stages=json.dumps(stages, ensure_ascii=False),
        )
        effects_task = DownloadTask(id=2, video_id=8, task_type="effects", status="processing", progress=50)
        db = FakeTaskDb([job], [effects_task])

        with patch("backend.api.automation.request_stage_task_control", return_value=1) as stage_control, \
                patch("backend.api.automation.request_job_control") as job_control:
            killed_count = _skip_current_effects_stage(db, job)

        self.assertEqual(killed_count, 1)
        stage_control.assert_called_once_with(db, effects_task, "skip")
        job_control.assert_not_called()
        self.assertEqual(effects_task.status, "skipped")
        self.assertEqual(effects_task.error_message, "用户跳过画面处理")
        self.assertFalse(json.loads(job.params)["enable_effects"])

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

        with patch("backend.api.automation.request_job_control", return_value=1) as control_mock:
            paused_count = _pause_batch_jobs(db, "batch-a")

        self.assertEqual(paused_count, 2)
        control_mock.assert_called_once_with(db, jobs[1], "pause")
        self.assertEqual(jobs[0].status, "paused")
        self.assertEqual(jobs[0].current_step, "批次暂停")
        self.assertEqual(jobs[1].status, "paused")
        self.assertTrue(json.loads(jobs[0].params)["batch_paused"])
        self.assertTrue(json.loads(jobs[1].params)["batch_paused"])
        self.assertEqual(jobs[3].status, "pending")

        resumed_ids = _resume_batch_jobs(db, "batch-a")

        self.assertEqual(resumed_ids, ["auto-1", "auto-2"])
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

    def test_pick_text_profile_uses_first_saved_profile_by_default(self):
        """一键流程未指定文本配置时自动使用首个已保存配置"""
        profiles = [
            TextProviderProfile(id=3, name="文本 B", provider_type="openai", base_url="https://b.example", api_key_encrypted="", model="b"),
            TextProviderProfile(id=1, name="文本 A", provider_type="openai", base_url="https://a.example", api_key_encrypted="", model="a"),
        ]
        db = FakeDb(profiles)

        profile = _pick_text_profile(db, None)

        self.assertEqual(profile.id, 3)

    def test_subtitle_download_candidates_are_deduplicated_by_language_and_type(self):
        """字幕候选会去重同语言多格式轨道，并保留首选语言优先级"""
        video = VideoSource(
            url="https://youtube.com/watch?v=test",
            subtitles=json.dumps([
                {"language": "zh-Hans", "type": "auto", "ext": "json3"},
                {"language": "zh-Hans", "type": "auto", "ext": "vtt"},
                {"language": "en", "type": "original", "ext": "vtt"},
                {"language": "ja", "type": "auto", "ext": "vtt"},
                {"language": "fil", "type": "auto", "ext": "vtt"},
            ], ensure_ascii=False),
        )

        candidates = _build_subtitle_download_candidates(video, "zh-Hans", "zh-Hans")
        pairs = [(candidate["language"], candidate["sub_type"]) for candidate in candidates]

        self.assertEqual(pairs.count(("zh-Hans", "auto")), 1)
        self.assertIn(("en", "original"), pairs)
        self.assertIn(("ja", "auto"), pairs)
        self.assertNotIn(("fil", "auto"), pairs)
        self.assertLess(pairs.index(("zh-Hans", "auto")), pairs.index(("en", "original")))

    def test_subtitle_download_falls_back_after_preferred_language_rate_limit(self):
        """首选字幕语言下载失败时会继续尝试候选轨道"""
        video = VideoSource(
            url="https://youtube.com/watch?v=test",
            subtitles=json.dumps([
                {"language": "en", "type": "original", "ext": "vtt"},
            ], ensure_ascii=False),
        )
        downloader = FakeSubtitleDownloader(success_language="en")

        with tempfile.TemporaryDirectory() as temp_dir:
            path, language, errors = _download_subtitle_with_fallback(
                downloader=downloader,
                video=video,
                requested_language="zh-Hans",
                preset_language="zh-Hans",
                output_dir=temp_dir,
                control_keys=["task:1"],
            )

        self.assertEqual(language, "en")
        self.assertTrue(path.endswith("subtitle.en.vtt"))
        self.assertGreater(len(errors), 0)
        self.assertEqual([call["language"] for call in downloader.calls[:4]], ["zh-Hans", "zh-CN", "zh", "en"])

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

    def test_subtitle_entries_to_voice_segments_extracts_speaker(self):
        """多人配音分段会保留说话人标签，并把标签从配音正文里去掉"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:01,500", "text": "旁白：进入战斗阶段"},
            {"index": 2, "start": "00:00:01,500", "end": "00:00:03,000", "text": "角色 A: 放技能"},
        ]

        segments = subtitle_entries_to_voice_segments(entries)

        self.assertEqual(segments[0]["speaker"], "旁白")
        self.assertEqual(segments[0]["text"], "进入战斗阶段")
        self.assertEqual(segments[1]["speaker"], "角色 A")
        self.assertEqual(segments[1]["text"], "放技能")

    def test_combine_original_and_translated_entries_for_double_line_display(self):
        """双行翻译显示用译文加原文，但不改变译文时间轴"""
        original = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "hello"},
            {"index": 2, "start": "00:00:02,000", "end": "00:00:03,000", "text": "world"},
        ]
        translated = [
            {"index": 1, "start": "00:00:01,100", "end": "00:00:02,200", "text": "你好"},
            {"index": 2, "start": "00:00:02,200", "end": "00:00:03,200", "text": "世界"},
        ]

        combined = combine_original_and_translated_entries(original, translated)

        self.assertEqual(combined[0]["start"], "00:00:01,100")
        self.assertEqual(combined[0]["text"], "你好\nhello")
        self.assertEqual(combined[1]["text"], "世界\nworld")

    def test_merge_subtitle_burn_preset_keeps_style_and_output_quality(self):
        """字幕烧录应同时保留字幕样式和一键流程的输出码率策略"""
        merged = merge_subtitle_burn_preset(
            {"font_name": "Microsoft YaHei", "font_size": 44, "secondary_color": "#FDE68A"},
            {
                "bitrate": {
                    "enabled": True,
                    "mode": "fixed",
                    "fixed_kbps": {"enabled": True, "random": False, "value": 2200, "min": 2200, "max": 2200},
                },
                "acceleration": {"enabled": True, "mode": "auto", "quality": "size"},
            },
        )

        self.assertEqual(merged["font_name"], "Microsoft YaHei")
        self.assertEqual(merged["secondary_color"], "#FDE68A")
        self.assertEqual(merged["bitrate"]["fixed_kbps"]["value"], 2200)
        self.assertEqual(merged["acceleration"]["quality"], "size")

    def test_build_final_export_preset_only_keeps_output_related_settings(self):
        """最终导出预设只保留导出分辨率和码率，不重复套用画面处理效果"""
        preset = build_final_export_preset({
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate_enabled": True,
            "bitrate_kbps": 2200,
        })

        self.assertFalse(preset["adjustments"]["enabled"])
        self.assertEqual(preset["canvas"]["resolution"], "1080p")
        self.assertTrue(preset["canvas"]["enabled"])
        self.assertFalse(preset["transform"]["enabled"])
        self.assertFalse(preset["timing"]["enabled"])
        self.assertEqual(preset["bitrate"]["fixed_kbps"]["value"], 2200)
        self.assertEqual(preset["acceleration"]["mode"], "auto")

    def test_should_apply_final_export_settings_only_when_export_settings_are_effective(self):
        """最终导出设置只在用户开启且设置了分辨率或码率时才额外执行"""
        export_settings = {
            "resolution": "1080p",
            "width": 1920,
            "height": 1080,
            "bitrate_enabled": True,
            "bitrate_kbps": 2200,
        }

        self.assertTrue(should_apply_final_export_settings(True, export_settings))
        self.assertFalse(should_apply_final_export_settings(False, export_settings))
        self.assertFalse(should_apply_final_export_settings(True, {"resolution": "original", "bitrate_enabled": False, "bitrate_kbps": 0}))

    def test_automation_export_stage_applies_final_export_settings_when_effects_disabled(self):
        """关闭画面处理但开启最终导出设置时，字幕或配音完成后仍按导出设置统一输出"""
        with tempfile.TemporaryDirectory(prefix="automation_export_render_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=1, platform="youtube", video_id="export-render", url="https://example.test/video", title="测试视频")
            task_ids = iter(range(1, 10))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation.ensure_project_dirs", return_value={"output_dir": temp_dir, "exports_dir": temp_dir}),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        export_with_settings=True,
                        export_settings={
                            "resolution": "1080p",
                            "width": 1920,
                            "height": 1080,
                            "bitrate_enabled": True,
                            "bitrate_kbps": 2200,
                        },
                        enable_voice=False,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["export"].status, "completed")
        self.assertEqual(fake_processor.burn_calls[0]["video_path"], downloaded_path)
        self.assertEqual(fake_processor.effects_calls[0]["video_path"], os.path.join(temp_dir, "subtitled.mp4"))
        self.assertFalse(fake_processor.effects_calls[0]["preset"]["transform"]["enabled"])
        self.assertEqual(fake_processor.effects_calls[0]["preset"]["canvas"]["resolution"], "1080p")

    def test_delete_job_record_removes_child_tasks_but_keeps_files(self):
        """删除素材记录只删数据库任务，不碰磁盘成品文件"""
        job = AutomationJobRecord(id="auto-delete", source_url="https://youtube.com/watch?v=1", status="completed")
        task = DownloadTask(id=9, video_id=1, task_type="export", status="completed", parent_job_id="auto-delete", output_path="D:\\video.mp4")
        db = FakeTaskDb([job], [task])

        _delete_job_record(db, job)

        self.assertEqual(db.jobs, [])
        self.assertEqual(db.tasks, [])
        self.assertEqual(db.commit_count, 1)

    def test_voice_for_segment_uses_speaker_map(self):
        """分段配音按说话人选择音色，未匹配时回退默认音色"""
        speaker_map = {"旁白": "alloy", "角色 A": "nova"}

        self.assertEqual(_voice_for_segment({"speaker": "角色 A"}, "onyx", speaker_map), "nova")
        self.assertEqual(_voice_for_segment({"speaker": "角色 B"}, "onyx", speaker_map), "onyx")

    def test_glossary_terms_and_banned_words(self):
        """术语字库会替换固定写法，禁词检测返回去重命中"""
        entries = [{"text": "LOL 的 DPS 很高，不能出现敏感词"}]

        processed = _apply_glossary_terms(entries, [
            {"source": "LOL", "replacement": "英雄联盟", "note": "游戏名"},
            {"source": "DPS", "replacement": "输出位", "note": ""},
        ])

        self.assertEqual(processed[0]["text"], "英雄联盟 的 输出位 很高，不能出现敏感词")
        self.assertEqual(_find_banned_words(processed[0]["text"], ["敏感词", "敏感词", "不存在"]), ["敏感词"])


if __name__ == "__main__":
    unittest.main()
