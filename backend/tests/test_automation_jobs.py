import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.automation import _apply_glossary_terms, _build_subtitle_download_candidates, _cancel_job, _create_automation_job, _default_stages, _delete_job_record, _download_subtitle_with_fallback, _find_banned_words, _get_batch_concurrency_from_job, _is_batch_paused, _job_folder_for_open, _job_to_response, _normalize_batch_urls, _pause_running_job, _pick_text_profile, _prepare_interrupted_job_for_startup, _prepare_job_export_stage_for_rerun, _restore_batch_runtime_state, _pause_batch_jobs, _prepare_job_for_resume, _register_batch_pause, _resume_batch_jobs, _reset_job_for_retry, _skip_current_effects_stage, _stage_output_if_reusable, _voice_for_segment, build_final_export_preset, combine_original_and_translated_entries, merge_subtitle_burn_preset, should_apply_final_export_settings, validate_automation_request_profiles, AutomationReExportRequest, AutomationRunRequest, BATCH_PAUSED, BATCH_SEMAPHORES, delete_automation_job_folder, reexport_automation_job, subtitle_entries_to_voice_segments  # noqa: E402
from backend.api.automation import _download_cover_asset, _run_automation_sync, list_automation_jobs, LocalVideoPreviewRequest, preview_local_video  # noqa: E402
from backend.models import AutomationJobRecord, DownloadTask, TextProviderProfile, VideoSource, VoiceProviderProfile  # noqa: E402
from backend.models.database import Base  # noqa: E402


class FakeQuery:
    """测试用查询对象，模拟 SQLAlchemy 的最小行为"""

    def __init__(self, jobs):
        self.jobs = jobs

    def order_by(self, *_):
        return self

    def filter(self, *_):
        return self

    def limit(self, *_):
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

    def __init__(self, jobs, tasks, videos=None):
        super().__init__(jobs)
        self.tasks = tasks
        self.videos = videos or []

    def query(self, model):
        if model is DownloadTask:
            return FakeQuery(self.tasks)
        if model is VideoSource:
            return FakeQuery(self.videos)
        return FakeQuery(self.jobs)

    def delete(self, item):
        if item in self.tasks:
            self.tasks.remove(item)
        elif item in self.jobs:
            self.jobs.remove(item)

    def refresh(self, _item):
        return None


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
        self.thumbnail_calls: list[dict] = []

    def download_video(self, **_):
        """返回已准备好的本地视频路径"""
        return self.download_path

    def download_subtitle(self, *_args, **_kwargs):
        """一键流程不应再下载字幕"""
        self.subtitle_download_calls += 1
        raise AssertionError("一键流程不应调用字幕下载")

    def download_thumbnail(self, **kwargs):
        """记录封面下载参数并返回假封面文件"""
        self.thumbnail_calls.append(kwargs)
        return os.path.join(kwargs["output_dir"], "cover.jpg")


class FailingLocalSourceDownloader:
    """测试用下载器，本地视频流程不应调用任何网络解析或下载"""

    def parse_video(self, *_args, **_kwargs):
        """本地视频不应调用 yt-dlp 解析"""
        raise AssertionError("本地视频流程不应调用链接解析")

    def download_video(self, *_args, **_kwargs):
        """本地视频不应调用 yt-dlp 下载"""
        raise AssertionError("本地视频流程不应调用视频下载")

    def download_thumbnail(self, *_args, **_kwargs):
        """本地视频没有网络封面，不应触发封面下载"""
        raise AssertionError("本地视频流程不应调用封面下载")


class FailingParseDownloader:
    """测试用下载器，模拟 YouTube 解析被机器人验证拦截"""

    def parse_video(self, *_args, **_kwargs):
        """模拟 yt-dlp 解析失败"""
        raise RuntimeError("视频解析失败: ERROR: [youtube] test: Sign in to confirm you're not a bot")


class FakeAutomationProcessor:
    """测试用媒体处理器，避免真正运行 ffmpeg"""

    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.burn_calls: list[dict] = []
        self.effects_calls: list[dict] = []
        self.merge_calls: list[dict] = []

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

    def merge_audio_video(self, **kwargs):
        """记录音频合并参数并返回假输出文件"""
        self.merge_calls.append(kwargs)
        output_path = os.path.join(self.temp_dir, "merged.mp4")
        with open(output_path, "wb") as file:
            file.write(b"merged")
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

    def test_job_response_exposes_reusable_subtitle_and_media_paths(self):
        """任务响应会补充可编辑字幕、重导出源视频和配音音轨路径"""
        with tempfile.TemporaryDirectory(prefix="automation_job_assets_") as temp_dir:
            download_path = os.path.join(temp_dir, "downloaded.mp4")
            subtitle_ass_path = os.path.join(temp_dir, "downloaded_zh.ass")
            voice_path = os.path.join(temp_dir, "voice.mp3")
            subtitled_video_path = os.path.join(temp_dir, "downloaded_subtitled.mp4")
            for path in (download_path, subtitle_ass_path, voice_path, subtitled_video_path):
                with open(path, "wb") as file:
                    file.write(b"ok")

            job = AutomationJobRecord(
                id="auto-assets",
                source_url="https://youtube.com/watch?v=test",
                title="测试任务",
                status="completed",
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": download_path, "error_message": None},
                    {"key": "subtitle", "status": "completed", "progress": 100, "task_id": 2, "output_path": subtitled_video_path, "error_message": None},
                    {"key": "voice", "status": "completed", "progress": 100, "task_id": 3, "output_path": voice_path, "error_message": None},
                ], ensure_ascii=False),
            )
            subtitle_task = DownloadTask(
                id=2,
                video_id=1,
                task_type="subtitle",
                status="completed",
                progress=100,
                output_path=subtitled_video_path,
                params=json.dumps({"editable_subtitle_path": subtitle_ass_path}, ensure_ascii=False),
                parent_job_id="auto-assets",
            )

            response = _job_to_response(job, FakeTaskDb([job], [subtitle_task]))

        self.assertEqual(response.subtitle_asset_path, subtitle_ass_path)
        self.assertEqual(response.source_video_path, download_path)
        self.assertEqual(response.voice_asset_path, voice_path)

    def test_job_response_prefers_comparison_subtitle_and_exposes_pair_paths(self):
        """字幕调整页优先拿中英对照字幕，同时暴露原文和译文路径用于旧任务补全"""
        with tempfile.TemporaryDirectory(prefix="automation_subtitle_pair_") as temp_dir:
            source_path = os.path.join(temp_dir, "source_en_local.srt")
            translated_path = os.path.join(temp_dir, "source_en_translated.srt")
            comparison_path = os.path.join(temp_dir, "source_en_comparison.srt")
            ass_path = os.path.join(temp_dir, "source_en.ass")
            for path in (source_path, translated_path, comparison_path, ass_path):
                with open(path, "w", encoding="utf-8") as file:
                    file.write("1\n00:00:00,000 --> 00:00:01,000\nhello\n")

            job = AutomationJobRecord(
                id="auto-subtitle-pair",
                source_url="https://youtube.com/watch?v=test",
                title="字幕对照任务",
                status="completed",
                stages=json.dumps([
                    {"key": "subtitle", "status": "completed", "progress": 100, "task_id": 7, "output_path": ass_path, "error_message": None},
                ], ensure_ascii=False),
            )
            subtitle_task = DownloadTask(
                id=7,
                video_id=1,
                task_type="subtitle",
                status="completed",
                progress=100,
                output_path=ass_path,
                params=json.dumps({
                    "source_subtitle_path": source_path,
                    "translated_subtitle_path": translated_path,
                    "comparison_subtitle_path": comparison_path,
                    "editable_subtitle_path": comparison_path,
                    "subtitle_ass_path": ass_path,
                }, ensure_ascii=False),
                parent_job_id="auto-subtitle-pair",
            )

            response = _job_to_response(job, FakeTaskDb([job], [subtitle_task]))

        self.assertEqual(response.subtitle_asset_path, comparison_path)
        self.assertEqual(response.source_subtitle_path, source_path)
        self.assertEqual(response.translated_subtitle_path, translated_path)

    def test_job_response_includes_cached_video_info(self):
        """任务响应会带上缓存视频信息，工作台刷新后不再一直显示准备中"""
        video = VideoSource(
            id=5,
            platform="youtube",
            video_id="D_OuGJETqQw",
            url="https://youtu.be/D_OuGJETqQw",
            title="200 Days in Minecraft Bedrock Edition",
            author="测试作者",
            duration=120,
            thumbnail_url="https://example.test/cover.jpg",
            formats=json.dumps([{"format_id": "18", "resolution": "360p"}], ensure_ascii=False),
            subtitles=json.dumps([
                {"language": f"lang-{index}", "name": "English", "ext": "vtt", "type": "auto"}
                for index in range(20)
            ], ensure_ascii=False),
        )
        job = AutomationJobRecord(
            id="auto-video-info",
            video_id=5,
            source_url="https://youtu.be/D_OuGJETqQw",
            title="200 Days in Minecraft Bedrock Edition",
            status="completed",
            progress=100,
        )

        response = _job_to_response(job, FakeTaskDb([job], [], [video]))

        self.assertIsNotNone(response.video_info)
        self.assertEqual(response.video_info["id"], 5)
        self.assertEqual(response.video_info["title"], "200 Days in Minecraft Bedrock Edition")
        self.assertEqual(response.video_info["format_count"], 1)
        self.assertEqual(response.video_info["formats"][0]["format_id"], "18")
        self.assertEqual(response.video_info["subtitle_count"], 20)
        self.assertEqual(len(response.video_info["subtitles"]), 12)
        self.assertEqual(response.video_info["subtitles"][0]["language"], "lang-0")

    def test_job_response_infers_parse_failure_for_legacy_job(self):
        """旧失败任务没有阶段错误时，响应层也要显示解析阶段失败"""
        job = AutomationJobRecord(
            id="auto-legacy-parse-failed",
            source_url="https://youtube.com/watch?v=test",
            title="一键自动流程",
            status="failed",
            progress=0,
            error_message="视频解析失败: ERROR: [youtube] test: Sign in to confirm you're not a bot",
            stages=json.dumps(_default_stages(), ensure_ascii=False),
        )

        response = _job_to_response(job)

        self.assertEqual(response.stages[0].key, "parse")
        self.assertEqual(response.stages[0].status, "failed")
        self.assertIn("视频解析失败", response.stages[0].error_message or "")

    def test_job_response_exposes_local_cover_asset_path(self):
        """任务响应会返回本地封面路径，素材库可以直接显示缩略图"""
        with tempfile.TemporaryDirectory(prefix="automation_cover_asset_") as temp_dir:
            cover_path = os.path.join(temp_dir, "cover.jpg")
            with open(cover_path, "wb") as file:
                file.write(b"cover")
            job = AutomationJobRecord(
                id="auto-cover-asset",
                source_url="https://youtube.com/watch?v=cover",
                title="封面素材任务",
                status="completed",
                params=json.dumps({"cover_asset_path": cover_path}, ensure_ascii=False),
            )

            response = _job_to_response(job)

        self.assertEqual(response.cover_asset_path, cover_path)

    def test_job_response_generates_thumbnail_from_completed_export(self):
        """本地完成素材没有封面时，会从成品视频生成一张缩略图"""
        with tempfile.TemporaryDirectory(prefix="automation_generated_thumb_") as temp_dir:
            output_path = os.path.join(temp_dir, "exports", "final.mp4")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as file:
                file.write(b"video")
            job = AutomationJobRecord(
                id="auto-generated-thumb",
                source_url="local:D:\\input.mp4",
                title="本地缩略图任务",
                status="completed",
                output_path=output_path,
                params=json.dumps({"workspace_dir": temp_dir}, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "task_id": None, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [])

            def fake_run(cmd, **_kwargs):
                """模拟 ffmpeg 截帧并写出缩略图"""
                with open(cmd[-1], "wb") as file:
                    file.write(b"jpg")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("backend.api.automation.get_ffmpeg_command", return_value="ffmpeg"), \
                    patch("backend.api.automation.subprocess.run", side_effect=fake_run) as run_mock:
                response = _job_to_response(job, db)

            params = json.loads(job.params)

        self.assertTrue(response.cover_asset_path.endswith("thumbnail.jpg"))
        self.assertEqual(params["cover_asset_path"], response.cover_asset_path)
        self.assertEqual(db.commit_count, 1)
        self.assertTrue(run_mock.called)

    def test_local_video_preview_generates_thumbnail_for_workspace(self):
        """工作台选择本地视频时会生成可展示的缩略图路径"""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory(prefix="automation_local_preview_") as temp_dir:
            source_path = os.path.join(temp_dir, "Local Preview.mp4")
            with open(source_path, "wb") as file:
                file.write(b"video")
            workspace_dir = os.path.join(temp_dir, "workspace")
            paths = {
                "workspace_dir": workspace_dir,
                "workspace_name": "local-preview",
                "downloads_dir": os.path.join(workspace_dir, "downloads"),
                "output_dir": os.path.join(workspace_dir, "output"),
                "exports_dir": os.path.join(workspace_dir, "exports"),
            }
            for directory in (paths["workspace_dir"], paths["downloads_dir"], paths["output_dir"], paths["exports_dir"]):
                os.makedirs(directory, exist_ok=True)

            def fake_run(cmd, **_kwargs):
                """模拟 ffmpeg 截帧并写出缩略图"""
                with open(cmd[-1], "wb") as file:
                    file.write(b"jpg")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            db = Session()
            try:
                with patch("backend.api.automation.ensure_video_workspace", return_value=paths), \
                        patch("backend.api.automation.get_ffmpeg_command", return_value="ffmpeg"), \
                        patch("backend.api.automation.subprocess.run", side_effect=fake_run):
                    response = preview_local_video(LocalVideoPreviewRequest(source=source_path), db)
                    self.assertEqual(response.platform, "local")
                    self.assertEqual(response.title, "Local Preview")
                    self.assertTrue(response.cover_asset_path.endswith("thumbnail.jpg"))
                    self.assertTrue(os.path.isfile(response.cover_asset_path))
            finally:
                db.close()

    def test_list_jobs_prunes_completed_item_when_export_file_is_missing(self):
        """本地成品文件夹被手动删除后，刷新任务列表会自动清理素材记录"""
        missing_output = os.path.join(tempfile.gettempdir(), "missing-library-output.mp4")
        job = AutomationJobRecord(
            id="auto-missing-library",
            source_url="https://youtube.com/watch?v=missing",
            title="已删除素材",
            status="completed",
            output_path=missing_output,
            stages=json.dumps([
                {"key": "export", "status": "completed", "progress": 100, "task_id": None, "output_path": missing_output, "error_message": None},
            ], ensure_ascii=False),
        )
        db = FakeTaskDb([job], [])

        response = list_automation_jobs(db)

        self.assertEqual(response, [])
        self.assertEqual(db.jobs, [])

    def test_list_jobs_keeps_completed_item_when_export_file_exists(self):
        """成品文件仍存在时，刷新任务列表不会误删素材记录"""
        with tempfile.TemporaryDirectory(prefix="automation_library_exists_") as temp_dir:
            output_path = os.path.join(temp_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")
            job = AutomationJobRecord(
                id="auto-existing-library",
                source_url="https://youtube.com/watch?v=exists",
                title="存在素材",
                status="completed",
                output_path=output_path,
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "task_id": None, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [])

            response = list_automation_jobs(db)

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0].id, "auto-existing-library")
        self.assertEqual(db.jobs, [job])

    def test_automation_cover_download_uses_custom_output_dir(self):
        """一键流程自动保存封面时使用用户选择的封面目录"""
        with tempfile.TemporaryDirectory(prefix="automation_cover_") as default_dir:
            with tempfile.TemporaryDirectory(prefix="automation_cover_custom_") as custom_dir:
                video = VideoSource(
                    id=6,
                    platform="youtube",
                    video_id="cover-test",
                    url="https://youtu.be/cover-test",
                    title="Cover Test",
                    thumbnail_url="https://example.test/cover.jpg",
                )
                downloader = FakeAutomationDownloader(os.path.join(default_dir, "video.mp4"))

                output_path = _download_cover_asset(
                    video,
                    downloader,
                    {"downloads_dir": default_dir},
                    custom_dir,
                )

        self.assertEqual(output_path, os.path.join(custom_dir, "cover.jpg"))
        self.assertEqual(downloader.thumbnail_calls[0]["output_dir"], custom_dir)
        self.assertEqual(downloader.thumbnail_calls[0]["thumbnail_url"], video.thumbnail_url)
        self.assertEqual(downloader.thumbnail_calls[0]["file_name"], "Cover_Test_cover")

    def test_automation_cover_download_defaults_to_workspace_root(self):
        """一键流程自动保存封面默认放到视频项目根目录，方便打开文件夹后直接看到"""
        with tempfile.TemporaryDirectory(prefix="automation_cover_root_") as workspace_dir:
            downloads_dir = os.path.join(workspace_dir, "downloads")
            os.makedirs(downloads_dir, exist_ok=True)
            video = VideoSource(
                id=7,
                platform="youtube",
                video_id="cover-root",
                url="https://youtu.be/cover-root",
                title="Cover Root",
                thumbnail_url="https://example.test/cover.jpg",
            )
            downloader = FakeAutomationDownloader(os.path.join(downloads_dir, "video.mp4"))

            output_path = _download_cover_asset(
                video,
                downloader,
                {"workspace_dir": workspace_dir, "downloads_dir": downloads_dir},
                None,
            )

        self.assertEqual(output_path, os.path.join(workspace_dir, "cover.jpg"))
        self.assertEqual(downloader.thumbnail_calls[0]["output_dir"], workspace_dir)

    def test_job_response_recovers_legacy_flat_subtitle_asset_path(self):
        """旧版平铺目录任务即使没保存字幕参数，也能从 output 目录回推出可编辑字幕"""
        with tempfile.TemporaryDirectory(prefix="automation_legacy_assets_") as temp_dir:
            downloads_dir = os.path.join(temp_dir, "downloads")
            output_dir = os.path.join(temp_dir, "output")
            exports_dir = os.path.join(temp_dir, "exports")
            os.makedirs(downloads_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(exports_dir, exist_ok=True)

            download_path = os.path.join(downloads_dir, "100 Days in Minecraft Bedrock Edition.mp4")
            subtitle_ass_path = os.path.join(output_dir, "100 Days in Minecraft Bedrock Edition_en.ass")
            subtitled_video_path = os.path.join(output_dir, "100 Days in Minecraft Bedrock Edition_subtitled.mp4")
            exported_video_path = os.path.join(exports_dir, "100 Days in Minecraft Bedrock Edition_subtitled.mp4")
            for path in (download_path, subtitle_ass_path, subtitled_video_path, exported_video_path):
                with open(path, "wb") as file:
                    file.write(b"ok")

            job = AutomationJobRecord(
                id="auto-legacy-assets",
                source_url="https://youtube.com/watch?v=test",
                title="100 Days in Minecraft Bedrock Edition",
                status="completed",
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": download_path, "error_message": None},
                    {"key": "subtitle", "status": "completed", "progress": 100, "task_id": 2, "output_path": subtitled_video_path, "error_message": None},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 3, "output_path": exported_video_path, "error_message": None},
                ], ensure_ascii=False),
            )
            subtitle_task = DownloadTask(
                id=2,
                video_id=1,
                task_type="subtitle",
                status="completed",
                progress=100,
                output_path=subtitled_video_path,
                params="{}",
                parent_job_id="auto-legacy-assets",
            )

            response = _job_to_response(job, FakeTaskDb([job], [subtitle_task]))

        self.assertEqual(response.subtitle_asset_path, subtitle_ass_path)
        self.assertEqual(response.source_video_path, download_path)

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

    def test_automation_translation_saves_comparison_subtitle_for_review(self):
        """一键翻译后单独保存校对用中英对照字幕，不受单行烧录预设影响"""
        class EnglishRecognizer:
            """测试用英文识别器"""

            def transcribe_video(self, video_path, progress_callback=None):
                if progress_callback:
                    progress_callback(100)
                return ([{"index": 1, "start": "00:00:00,000", "end": "00:00:01,200", "text": "hello world"}], "en")

        captured_text_settings: dict[str, dict] = {}

        class FakeTextEngine:
            """测试用文本引擎，模拟翻译接口返回中文"""

            async def process_subtitle_entries(self, entries, **kwargs):
                captured_text_settings["settings"] = kwargs.get("settings") or {}
                return [{**entry, "text": "你好世界"} for entry in entries]

        with tempfile.TemporaryDirectory(prefix="automation_translate_review_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            video = VideoSource(id=1, platform="youtube", video_id="translate-save", url="https://example.test/video", title="Translate Save")
            text_profile = TextProviderProfile(
                id=11,
                name="测试文本",
                provider_type="openai_compatible",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                model="test-model",
                extra_params='{"system_prompt": "旧配置提示词"}',
            )
            task_ids = iter(range(20, 30))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "translate-save",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=EnglishRecognizer()),
                patch("backend.api.automation.TextEngine", return_value=FakeTextEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=text_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        processing_preset={},
                        enable_voice=False,
                        burn_subtitles=False,
                        subtitle_operation="translate",
                        subtitle_target_language="zh-CN",
                        text_system_prompt="独立提示词预设",
                        text_profile_id=11,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

            comparison_path = os.path.join(temp_dir, "downloaded_en_comparison.srt")
            translated_path = os.path.join(temp_dir, "downloaded_en_translated.srt")
            ass_path = os.path.join(temp_dir, "downloaded_en.ass")

            self.assertTrue(os.path.isfile(comparison_path))
            self.assertTrue(os.path.isfile(translated_path))
            self.assertTrue(os.path.isfile(ass_path))
            self.assertEqual(captured_text_settings["settings"]["system_prompt"], "独立提示词预设")
            with open(comparison_path, "r", encoding="utf-8") as file:
                comparison_content = file.read()

        self.assertIn("你好世界", comparison_content)
        self.assertIn("hello world", comparison_content)

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

    def test_prepare_job_export_stage_for_rerun_only_resets_export(self):
        """字幕调整页重新导出只重置导出阶段，不动下载/画面/字幕/配音结果"""
        job = AutomationJobRecord(
            id="auto-rerun-export",
            source_url="https://youtube.com/watch?v=test",
            status="completed",
            progress=100,
            current_step="流程完成",
            output_path="D:/old.mp4",
            error_message="旧错误",
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
                {"key": "subtitle", "status": "completed", "progress": 100, "task_id": 2, "output_path": "D:/subtitle.ass", "error_message": None},
                {"key": "export", "status": "completed", "progress": 100, "task_id": 3, "output_path": "D:/old.mp4", "error_message": None},
            ], ensure_ascii=False),
        )

        _prepare_job_export_stage_for_rerun(job)
        stages = {stage["key"]: stage for stage in json.loads(job.stages)}

        self.assertEqual(job.status, "running")
        self.assertEqual(job.current_step, "字幕调整重新导出")
        self.assertIsNone(job.output_path)
        self.assertEqual(stages["download"]["status"], "completed")
        self.assertEqual(stages["subtitle"]["status"], "completed")
        self.assertEqual(stages["export"]["status"], "pending")
        self.assertIsNone(stages["export"]["output_path"])

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

    def test_cancelled_job_response_prefers_latest_cancel_message(self):
        """已取消任务返回时用当前取消原因覆盖阶段里的旧中断提示"""
        job = AutomationJobRecord(
            id="auto-cancel-message",
            source_url="https://youtube.com/watch?v=test",
            status="cancelled",
            current_step="已取消",
            error_message="用户取消",
            stages=json.dumps([
                {"key": "parse", "status": "cancelled", "progress": 0, "task_id": None, "output_path": None, "error_message": "后端重启前任务已中断，请点击继续重新执行"},
                {"key": "download", "status": "cancelled", "progress": 0, "task_id": None, "output_path": None, "error_message": "后端重启前任务已中断，请点击继续重新执行"},
            ], ensure_ascii=False),
        )

        response = _job_to_response(job)

        self.assertEqual(response.error_message, "用户取消")
        self.assertTrue(all(stage.error_message == "用户取消" for stage in response.stages))

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

    def test_validate_automation_requires_text_profile_for_translate(self):
        """字幕翻译策略没有文本 API 配置时，启动前直接拦截"""
        with self.assertRaises(HTTPException) as context:
            validate_automation_request_profiles(
                FakeDb([]),
                AutomationRunRequest(url="https://youtube.com/watch?v=test", subtitle_operation="translate"),
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("文本 API", context.exception.detail)

    def test_validate_automation_rejects_empty_text_api_key(self):
        """文本配置存在但没有密钥时不能进入后台任务"""
        profile = TextProviderProfile(
            id=1,
            name="空密钥文本",
            provider_type="openai_compatible",
            base_url="https://api.example.com/v1",
            api_key_encrypted="empty",
            model="gpt-test",
        )

        with patch("backend.api.automation.decrypt_api_key", return_value=""):
            with self.assertRaises(HTTPException) as context:
                validate_automation_request_profiles(
                    FakeDb([profile]),
                    AutomationRunRequest(url="https://youtube.com/watch?v=test", subtitle_operation="polish"),
                )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("API Key", context.exception.detail)

    def test_validate_automation_requires_voice_profile_when_enabled(self):
        """开启配音但没有配音配置时，启动前直接拦截"""
        with self.assertRaises(HTTPException) as context:
            validate_automation_request_profiles(
                FakeDb([]),
                AutomationRunRequest(url="https://youtube.com/watch?v=test", subtitle_operation="none", enable_voice=True),
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("配音", context.exception.detail)

    def test_validate_automation_accepts_saved_voice_profile(self):
        """配音配置完整时允许一键流程继续启动"""
        profile = VoiceProviderProfile(
            id=1,
            name="配音",
            provider_type="custom_tts",
            base_url="https://api.example.com/v1",
            api_key_encrypted="encrypted",
            voice="gpt-4o-mini-tts",
        )

        with patch("backend.api.automation.decrypt_api_key", return_value="sk-test"):
            validate_automation_request_profiles(
                FakeDb([profile]),
                AutomationRunRequest(url="https://youtube.com/watch?v=test", subtitle_operation="none", enable_voice=True),
            )

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

    def test_reexport_automation_job_uses_new_subtitle_and_updates_job_output(self):
        """字幕调整页重新导出会使用新 ASS 并覆盖任务的最新导出路径"""
        with tempfile.TemporaryDirectory(prefix="automation_reexport_") as temp_dir:
            source_video_path = os.path.join(temp_dir, "source.mp4")
            subtitle_path = os.path.join(temp_dir, "manual.ass")
            voice_path = os.path.join(temp_dir, "voice.mp3")
            for path in (source_video_path, voice_path):
                with open(path, "wb") as file:
                    file.write(b"data")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,手动字幕
""")

            job = AutomationJobRecord(
                id="auto-reexport",
                video_id=7,
                source_url="https://youtube.com/watch?v=test",
                status="completed",
                output_path=os.path.join(temp_dir, "old.mp4"),
                params=json.dumps({
                    "output_format": "mp4",
                    "export_with_settings": False,
                    "audio_mode": "mix",
                    "original_volume": 0.35,
                    "export_settings": {"resolution": "original", "bitrate_enabled": False, "bitrate_kbps": 0},
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": source_video_path, "error_message": None},
                    {"key": "voice", "status": "completed", "progress": 100, "task_id": 2, "output_path": voice_path, "error_message": None},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 3, "output_path": os.path.join(temp_dir, "old.mp4"), "error_message": None},
                ], ensure_ascii=False),
            )
            fake_processor = FakeAutomationProcessor(temp_dir)
            db = FakeTaskDb([job], [])
            task_ids = iter(range(30, 40))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
            ):
                response = reexport_automation_job(
                    "auto-reexport",
                    AutomationReExportRequest(subtitle_path=subtitle_path, audio_path=voice_path),
                    db,
                )

        self.assertEqual(response.job_id, "auto-reexport")
        self.assertTrue(response.output_path.endswith("exported.mp4"))
        self.assertNotEqual(fake_processor.burn_calls[0]["subtitle_path"], subtitle_path)
        self.assertTrue(fake_processor.burn_calls[0]["subtitle_path"].endswith("_manual_clean.ass"))
        self.assertEqual(fake_processor.merge_calls[0]["audio_path"], voice_path)
        self.assertEqual(job.output_path, response.output_path)
        self.assertEqual(job.status, "completed")

    def test_reexport_automation_job_cleans_subtitle_punctuation_before_burn(self):
        """重新导出旧字幕时会先清理逗号、句号、省略号和顿号再烧录"""
        with tempfile.TemporaryDirectory(prefix="automation_reexport_clean_") as temp_dir:
            source_video_path = os.path.join(temp_dir, "source.mp4")
            subtitle_path = os.path.join(temp_dir, "manual.ass")
            with open(source_video_path, "wb") as file:
                file.write(b"video")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,我已经度过了前100天，等等...还有、顿号。
""")

            job = AutomationJobRecord(
                id="auto-reexport-clean",
                video_id=8,
                source_url="https://youtube.com/watch?v=clean",
                status="completed",
                params=json.dumps({"output_format": "mp4", "export_with_settings": False}, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": source_video_path, "error_message": None},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 2, "output_path": os.path.join(temp_dir, "old.mp4"), "error_message": None},
                ], ensure_ascii=False),
            )
            fake_processor = FakeAutomationProcessor(temp_dir)
            db = FakeTaskDb([job], [])
            task_ids = iter(range(40, 50))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
            ):
                reexport_automation_job("auto-reexport-clean", AutomationReExportRequest(subtitle_path=subtitle_path), db)

            cleaned_path = fake_processor.burn_calls[0]["subtitle_path"]
            with open(cleaned_path, "r", encoding="utf-8") as file:
                cleaned_content = file.read()

            dialogue_text = cleaned_content.split("Dialogue:", 1)[1].rsplit(",,", 1)[1]
            self.assertNotRegex(dialogue_text, r"[，。、,.]|\.{3,}|…")
            self.assertIn("我已经度过了前100天 等等 还有 顿号", dialogue_text)

    def test_delete_job_record_removes_child_tasks_but_keeps_files(self):
        """删除素材记录只删数据库任务，不碰磁盘成品文件"""
        job = AutomationJobRecord(id="auto-delete", source_url="https://youtube.com/watch?v=1", status="completed")
        task = DownloadTask(id=9, video_id=1, task_type="export", status="completed", parent_job_id="auto-delete", output_path="D:\\video.mp4")
        db = FakeTaskDb([job], [task])

        _delete_job_record(db, job)

        self.assertEqual(db.jobs, [])
        self.assertEqual(db.tasks, [])
        self.assertEqual(db.commit_count, 1)

    def test_local_video_source_runs_full_automation_flow(self):
        """本地视频会复制到下载阶段目录，并继续走一键流程"""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory(prefix="automation_local_source_") as temp_dir:
            source_path = os.path.join(temp_dir, "Local Clip.mp4")
            with open(source_path, "wb") as file:
                file.write(b"local-video")

            workspace_dir = os.path.join(temp_dir, "videos", "local-flow__Local_Clip")
            downloads_dir = os.path.join(workspace_dir, "downloads")
            output_dir = os.path.join(workspace_dir, "output")
            exports_dir = os.path.join(workspace_dir, "exports")
            for directory in (downloads_dir, output_dir, exports_dir):
                os.makedirs(directory, exist_ok=True)

            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            db = Session()
            try:
                with (
                    patch("backend.api.automation.assert_required_tools_available"),
                    patch("backend.api.automation.Downloader", return_value=FailingLocalSourceDownloader()),
                    patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                    patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                    patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                    patch("backend.api.automation._pick_text_profile", return_value=None),
                    patch("backend.api.automation.ensure_video_workspace", return_value={
                        "workspace_dir": workspace_dir,
                        "workspace_name": "local-flow__Local_Clip",
                        "downloads_dir": downloads_dir,
                        "output_dir": output_dir,
                        "exports_dir": exports_dir,
                    }),
                ):
                    response = _run_automation_sync(
                        AutomationRunRequest(
                            url=f"local:{source_path}",
                            enable_effects=False,
                            processing_preset={},
                            enable_voice=False,
                            burn_subtitles=True,
                            output_format="mp4",
                        ),
                        db,
                    )
            finally:
                db.close()

            copied_path = os.path.join(downloads_dir, "Local Clip.mp4")
            stage_by_key = {stage.key: stage for stage in response.stages}

            self.assertTrue(os.path.isfile(copied_path))
            self.assertEqual(response.title, "Local Clip")
            self.assertTrue(os.path.isfile(response.output_path))
            self.assertEqual(stage_by_key["download"].output_path, copied_path)
            self.assertEqual(fake_recognizer.video_paths, [copied_path])
            self.assertEqual(stage_by_key["subtitle"].status, "completed")
            self.assertEqual(stage_by_key["export"].status, "completed")

    def test_run_automation_marks_parse_stage_failed_when_parse_raises(self):
        """一键流程解析失败时必须写入 parse 阶段错误，避免界面继续显示 pending"""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            request = AutomationRunRequest(
                url="https://youtube.com/watch?v=test",
                enable_effects=False,
                processing_preset={},
                enable_voice=False,
                burn_subtitles=True,
                output_format="mp4",
            )
            job = _create_automation_job(db, request)

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=FailingParseDownloader()),
                patch("backend.api.automation.FFmpegProcessor", return_value=object()),
            ):
                with self.assertRaises(RuntimeError):
                    _run_automation_sync(request, db, job)

            db.refresh(job)
            stages = {stage["key"]: stage for stage in json.loads(job.stages)}

            self.assertEqual(stages["parse"]["status"], "failed")
            self.assertIn("视频解析失败", stages["parse"]["error_message"])
            self.assertEqual(stages["download"]["status"], "pending")
        finally:
            db.close()

    def test_open_job_folder_prefers_video_workspace_root(self):
        """素材库打开文件夹应打开单视频根目录，而不是 exports 子目录"""
        with tempfile.TemporaryDirectory(prefix="automation_open_folder_") as temp_dir:
            videos_dir = os.path.join(temp_dir, "videos")
            workspace_dir = os.path.join(videos_dir, "video-1__测试视频")
            exports_dir = os.path.join(workspace_dir, "exports")
            output_dir = os.path.join(workspace_dir, "output")
            downloads_dir = os.path.join(workspace_dir, "downloads")
            for directory in (exports_dir, output_dir, downloads_dir):
                os.makedirs(directory, exist_ok=True)
            output_path = os.path.join(exports_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")

            job = AutomationJobRecord(
                id="auto-open-folder",
                source_url="https://youtube.com/watch?v=1",
                status="completed",
                output_path=output_path,
                params=json.dumps({
                    "workspace_dir": workspace_dir,
                    "video_downloads_dir": downloads_dir,
                    "video_output_dir": output_dir,
                    "video_exports_dir": exports_dir,
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )

            self.assertEqual(_job_folder_for_open(job), workspace_dir)

    def test_open_job_folder_recovers_workspace_from_export_path(self):
        """旧参数缺失时也能从 videos/<项目>/exports/成品 反推单视频根目录"""
        with tempfile.TemporaryDirectory(prefix="automation_open_detect_") as temp_dir:
            videos_dir = os.path.join(temp_dir, "videos")
            workspace_dir = os.path.join(videos_dir, "video-2__测试视频")
            exports_dir = os.path.join(workspace_dir, "exports")
            output_dir = os.path.join(workspace_dir, "output")
            downloads_dir = os.path.join(workspace_dir, "downloads")
            for directory in (exports_dir, output_dir, downloads_dir):
                os.makedirs(directory, exist_ok=True)
            output_path = os.path.join(exports_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")

            job = AutomationJobRecord(
                id="auto-open-detect",
                source_url="https://youtube.com/watch?v=2",
                status="completed",
                output_path=output_path,
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )

            self.assertEqual(_job_folder_for_open(job), workspace_dir)

    def test_open_job_folder_falls_back_to_legacy_exports_dir(self):
        """旧版公共 exports 成品没有独立目录时，打开文件所在目录而不是报错"""
        with tempfile.TemporaryDirectory(prefix="automation_open_legacy_") as temp_dir:
            exports_dir = os.path.join(temp_dir, "exports")
            os.makedirs(exports_dir, exist_ok=True)
            output_path = os.path.join(exports_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")

            job = AutomationJobRecord(
                id="auto-open-legacy",
                source_url="https://youtube.com/watch?v=3",
                status="completed",
                output_path=output_path,
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )

            self.assertEqual(_job_folder_for_open(job), exports_dir)

    def test_delete_job_folder_removes_workspace_and_record(self):
        """删除素材文件夹只允许删除单视频独立目录，并同步清理记录"""
        with tempfile.TemporaryDirectory(prefix="automation_delete_folder_") as temp_dir:
            videos_dir = os.path.join(temp_dir, "videos")
            workspace_dir = os.path.join(videos_dir, "video-1__测试视频")
            exports_dir = os.path.join(workspace_dir, "exports")
            output_dir = os.path.join(workspace_dir, "output")
            downloads_dir = os.path.join(workspace_dir, "downloads")
            for directory in (exports_dir, output_dir, downloads_dir):
                os.makedirs(directory, exist_ok=True)
            output_path = os.path.join(exports_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")

            job = AutomationJobRecord(
                id="auto-delete-folder",
                source_url="https://youtube.com/watch?v=1",
                status="completed",
                output_path=output_path,
                params=json.dumps({
                    "workspace_dir": workspace_dir,
                    "workspace_name": "video-1__测试视频",
                    "video_downloads_dir": downloads_dir,
                    "video_output_dir": output_dir,
                    "video_exports_dir": exports_dir,
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 9, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )
            task = DownloadTask(id=9, video_id=1, task_type="export", status="completed", parent_job_id=job.id, output_path=output_path)
            db = FakeTaskDb([job], [task])

            with patch("backend.api.automation.ensure_project_dirs", return_value={
                "project_root": temp_dir,
                "videos_dir": videos_dir,
                "downloads_dir": os.path.join(temp_dir, "downloads"),
                "output_dir": os.path.join(temp_dir, "output"),
                "exports_dir": os.path.join(temp_dir, "exports"),
                "data_dir": os.path.join(temp_dir, "data"),
                "default_project_root": temp_dir,
            }):
                response = delete_automation_job_folder(job.id, db)

            self.assertFalse(os.path.exists(workspace_dir))
            self.assertTrue(os.path.isdir(videos_dir))
            self.assertEqual(db.jobs, [])
            self.assertEqual(db.tasks, [])
            self.assertEqual(response.folder_path, workspace_dir)

    def test_delete_job_folder_rejects_legacy_public_exports_dir(self):
        """旧版公共 exports 目录不能整目录删除，避免误删其它成品"""
        with tempfile.TemporaryDirectory(prefix="automation_legacy_delete_") as temp_dir:
            exports_dir = os.path.join(temp_dir, "exports")
            os.makedirs(exports_dir, exist_ok=True)
            output_path = os.path.join(exports_dir, "final.mp4")
            with open(output_path, "wb") as file:
                file.write(b"video")

            job = AutomationJobRecord(
                id="auto-legacy-delete",
                source_url="https://youtube.com/watch?v=1",
                status="completed",
                output_path=output_path,
                stages=json.dumps([
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 9, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [])

            with patch("backend.api.automation.ensure_project_dirs", return_value={
                "project_root": temp_dir,
                "videos_dir": os.path.join(temp_dir, "videos"),
                "downloads_dir": os.path.join(temp_dir, "downloads"),
                "output_dir": os.path.join(temp_dir, "output"),
                "exports_dir": exports_dir,
                "data_dir": os.path.join(temp_dir, "data"),
                "default_project_root": temp_dir,
            }):
                with self.assertRaises(HTTPException) as context:
                    delete_automation_job_folder(job.id, db)

            self.assertEqual(context.exception.status_code, 400)
            self.assertTrue(os.path.exists(output_path))
            self.assertEqual(db.jobs, [job])

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
