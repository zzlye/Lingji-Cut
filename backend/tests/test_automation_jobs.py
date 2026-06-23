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

from backend.api.automation import _apply_glossary_terms, _audio_merge_volume, _build_auto_style_selector, _build_auto_voice_selector, _build_gemini_transcriber, _build_subtitle_download_candidates, _cancel_job, _create_automation_job, _default_stages, _delete_job_record, _download_subtitle_with_fallback, _find_banned_words, _gemini_align_timeline_profile, _get_batch_concurrency_from_job, _is_batch_paused, _job_folder_for_open, _job_to_response, _load_gemini_align_timeline_cache, _normalize_batch_urls, _pause_running_job, _pick_text_profile, _prepare_interrupted_job_for_startup, _prepare_job_export_stage_for_rerun, _recognize_subtitle_entries, _restore_batch_runtime_state, _pause_batch_jobs, _prepare_job_for_resume, _register_batch_pause, _resume_batch_jobs, _reset_job_for_retry, _skip_current_effects_stage, _stage_output_if_reusable, _subtitle_recognition_stage_progress, _subtitle_text_stage_progress, _sync_subtitle_entries_to_voice_timeline, _voice_for_segment, build_final_export_preset, combine_original_and_translated_entries, merge_subtitle_burn_preset, should_apply_final_export_settings, validate_automation_request_profiles, AutomationReExportRequest, AutomationRunRequest, BACKEND_RESTART_INTERRUPTED_MESSAGE, BATCH_PAUSED, BATCH_SEMAPHORES, delete_automation_job_folder, recover_automation_jobs_on_startup, reexport_automation_job, subtitle_entries_to_voice_segments  # noqa: E402
from backend.api.automation import _download_cover_asset, _job_workspace_paths, _run_automation_sync, list_automation_jobs, LocalVideoPreviewRequest, preview_local_video  # noqa: E402
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

    def close(self):
        pass


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


class FakeOriginalSubtitleDownloader(FakeAutomationDownloader):
    """测试用下载器，模拟 YouTube 原字幕下载成功"""

    def download_subtitle(self, url, language, output_dir, sub_type, control_keys):
        """写入一份可解析的 SRT 原字幕"""
        _ = (url, sub_type, control_keys)
        self.subtitle_download_calls += 1
        subtitle_path = os.path.join(output_dir, f"subtitle.{language}.srt")
        with open(subtitle_path, "w", encoding="utf-8") as file:
            file.write("1\n00:00:00,000 --> 00:00:01,000\n原字幕第一句\n\n")
        return subtitle_path


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
        self.convert_calls: list[dict] = []

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

    def convert_format(self, **kwargs):
        """返回假导出文件"""
        self.convert_calls.append(kwargs)
        output_path = kwargs.get("output_path") or os.path.join(self.temp_dir, "exported.mp4")
        with open(output_path, "wb") as file:
            file.write(b"exported")
        return output_path

    def media_video_size(self, *_args, **_kwargs):
        """返回固定视频尺寸，避免测试里真正调用 ffprobe"""
        return (1920, 1080)


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


class FakeVoiceEngine:
    """测试用配音引擎，返回假音频文件"""

    async def generate_grouped_timed_voice_track(self, output_path, progress_callback=None, **_kwargs):
        """模拟按时间轴分组合成配音"""
        if progress_callback:
            progress_callback(100)
        with open(output_path, "wb") as file:
            file.write(b"voice")
        return output_path

    async def generate_batched_timed_voice_track(self, output_path, progress_callback=None, **_kwargs):
        """模拟按字幕批量分段生成配音"""
        if progress_callback:
            progress_callback(100)
        with open(output_path, "wb") as file:
            file.write(b"voice")
        return output_path

    async def generate_timed_voice_track(self, output_path, progress_callback=None, **_kwargs):
        """模拟按字幕分段生成配音"""
        if progress_callback:
            progress_callback(100)
        with open(output_path, "wb") as file:
            file.write(b"voice")
        return output_path

    async def generate_voice(self, output_path, **_kwargs):
        """模拟整段生成配音"""
        with open(output_path, "wb") as file:
            file.write(b"voice")
        return output_path


class FailingTextEngine:
    """测试用文本引擎，模拟字幕翻译重试耗尽"""

    async def process_subtitle_entries(self, *_args, **_kwargs):
        """模拟按条目翻译失败"""
        raise RuntimeError("文本 API 重试次数已用完")

    async def process_text(self, *_args, **_kwargs):
        """模拟整段兜底翻译也失败"""
        raise RuntimeError("文本 API 重试次数已用完")


class FailingVoiceEngine:
    """测试用配音引擎，模拟分段和整段配音都失败"""

    async def generate_grouped_timed_voice_track(self, *_args, **_kwargs):
        """模拟分组配音失败"""
        raise RuntimeError("配音 API 重试次数已用完")

    async def generate_batched_timed_voice_track(self, *_args, **_kwargs):
        """模拟批量配音失败"""
        raise RuntimeError("配音 API 重试次数已用完")

    async def generate_timed_voice_track(self, *_args, **_kwargs):
        """模拟分段配音失败"""
        raise RuntimeError("配音 API 重试次数已用完")

    async def generate_voice(self, *_args, **_kwargs):
        """模拟整段配音失败"""
        raise RuntimeError("配音 API 重试次数已用完")


class FailingSegmentVoiceEngine:
    """测试用配音引擎，分段失败但整段接口不应被回退调用"""

    async def generate_grouped_timed_voice_track(self, *_args, **_kwargs):
        """模拟分组配音失败"""
        raise RuntimeError("分组配音失败")

    async def generate_batched_timed_voice_track(self, *_args, **_kwargs):
        """模拟按时间轴配音失败"""
        raise RuntimeError("批量配音失败")

    async def generate_timed_voice_track(self, *_args, **_kwargs):
        """模拟逐句配音失败"""
        raise RuntimeError("逐句配音失败")

    async def generate_voice(self, *_args, **_kwargs):
        """时间轴配音失败后不能回退整段生成错位音轨"""
        raise AssertionError("时间轴配音失败后不应回退整段配音")


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

    def test_gemini_subtitle_progress_does_not_stay_at_old_local_asr_22_percent(self):
        """Gemini 字幕识别不能沿用本地 ASR 的 10-35 映射，否则远程识别时界面会一直停在 22%"""
        self.assertEqual(_subtitle_recognition_stage_progress("local", 50), 22.5)
        self.assertEqual(_subtitle_recognition_stage_progress("gemini_align", 50), 32.5)
        self.assertEqual(_subtitle_recognition_stage_progress("gemini_align", 100), 55)
        self.assertEqual(_subtitle_text_stage_progress("gemini_align", 100), 70)

    def test_build_gemini_transcriber_uses_frontend_audio_split_settings(self):
        """前端 Gemini 切片参数必须传到识别器，避免界面设置不生效"""
        profile = TextProviderProfile(
            id=31,
            name="Gemini",
            provider_type="openai_compatible",
            base_url="https://example.test/v1",
            api_key_encrypted="encrypted",
            model="gemini-test",
            extra_params=json.dumps({"audio_concurrency": 8, "audio_timeout_seconds": 900}, ensure_ascii=False),
        )
        request = AutomationRunRequest(
            url="https://youtube.com/watch?v=test",
            text_profile_id=31,
            subtitle_recognition_mode="gemini_align",
            gemini_audio_segment_seconds=55,
            gemini_audio_overlap_seconds=0.8,
            gemini_audio_full_coverage=False,
            gemini_audio_concurrency=3,
            gemini_audio_timeout_seconds=180,
        )

        with patch("backend.api.automation.decrypt_api_key", return_value="sk-test"):
            transcriber = _build_gemini_transcriber(FakeDb([profile]), request)

        self.assertEqual(transcriber.settings["segment_seconds"], 55)
        self.assertEqual(transcriber.settings["segment_overlap_seconds"], 0.8)
        self.assertFalse(transcriber.settings["full_coverage"])
        self.assertEqual(transcriber.settings["audio_concurrency"], 3)
        self.assertEqual(transcriber.settings["audio_timeout_seconds"], 180)

    def test_job_response_exposes_reusable_subtitle_and_media_paths(self):
        """任务响应会补充可编辑字幕、重导出源视频和配音音轨路径"""
        with tempfile.TemporaryDirectory(prefix="automation_job_assets_") as temp_dir:
            download_path = os.path.join(temp_dir, "downloaded.mp4")
            subtitle_ass_path = os.path.join(temp_dir, "downloaded_zh.ass")
            voice_path = os.path.join(temp_dir, "voice.mp3")
            subtitle_only_path = os.path.join(temp_dir, "subtitle_only.mp4")
            subtitled_video_path = os.path.join(temp_dir, "downloaded_subtitled.mp4")
            for path in (download_path, subtitle_ass_path, voice_path, subtitle_only_path, subtitled_video_path):
                with open(path, "wb") as file:
                    file.write(b"ok")

            job = AutomationJobRecord(
                id="auto-assets",
                source_url="https://youtube.com/watch?v=test",
                title="测试任务",
                status="completed",
                params=json.dumps({"subtitle_only_video_path": subtitle_only_path}, ensure_ascii=False),
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
        self.assertEqual(response.subtitle_only_video_path, subtitle_only_path)

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

    def test_legacy_job_workspace_lookup_does_not_create_missing_folder(self):
        """旧任务缺少工作目录参数时，只读查询不能反向创建空项目文件夹"""
        with tempfile.TemporaryDirectory(prefix="automation_workspace_lookup_") as temp_dir:
            video = VideoSource(
                id=9,
                platform="youtube",
                video_id="local-asr",
                url="https://example.test/video",
                title="测试视频",
            )
            job = AutomationJobRecord(
                id="auto-missing-workspace",
                video_id=9,
                source_url=video.url,
                title=video.title,
                status="completed",
            )

            with patch("backend.core.paths.load_project_root", return_value=temp_dir):
                paths = _job_workspace_paths(job, FakeTaskDb([job], [], [video]))

            self.assertIsNone(paths)
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "videos")))

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

    def test_list_jobs_prunes_failed_item_when_all_assets_are_missing(self):
        """失败素材如果本地产物都没了，刷新列表时应自动清理"""
        job = AutomationJobRecord(
            id="auto-missing-failed",
            source_url="https://youtube.com/watch?v=failed",
            title="失败且已删",
            status="failed",
            output_path="D:/missing/final.mp4",
            stages=json.dumps([
                {"key": "download", "status": "failed", "progress": 40, "task_id": None, "output_path": None, "error_message": "中断"},
                {"key": "export", "status": "failed", "progress": 10, "task_id": None, "output_path": None, "error_message": "中断"},
            ], ensure_ascii=False),
        )
        db = FakeTaskDb([job], [])

        response = list_automation_jobs(db)

        self.assertEqual(response, [])
        self.assertEqual(db.jobs, [])

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
            video = VideoSource(id=1, platform="youtube", video_id="local-asr", url="https://youtu.be/local-asr?feature=share", title="测试视频")
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "local-asr",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }
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
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
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
            link_files = [name for name in os.listdir(temp_dir) if name.endswith(".txt")]
            self.assertEqual(link_files, ["youtube_link.txt"])
            with open(os.path.join(temp_dir, "youtube_link.txt"), "r", encoding="utf-8") as file:
                self.assertEqual(file.read().strip(), video.url)

    def test_automation_updates_subtitle_stage_during_burn(self):
        """字幕烧录时应同步 ffmpeg 进度，避免长视频界面一直停在 70%"""
        with tempfile.TemporaryDirectory(prefix="automation_burn_progress_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            job = AutomationJobRecord(
                id="auto-burn-progress",
                source_url="https://example.test/video",
                title="字幕烧录进度",
                status="pending",
                stages=json.dumps(_default_stages(), ensure_ascii=False),
            )

            class ProgressBurnProcessor(FakeAutomationProcessor):
                """测试用处理器，模拟 ffmpeg 烧录过程回传 50%"""

                def __init__(self, temp_dir: str, observed_job: AutomationJobRecord):
                    super().__init__(temp_dir)
                    self.observed_job = observed_job
                    self.observed_stage_progress = None
                    self.received_progress_callback = False

                def burn_subtitles(self, **kwargs):
                    """触发烧录进度回调并记录阶段进度"""
                    callback = kwargs.get("progress_callback")
                    self.received_progress_callback = callable(callback)
                    if callback:
                        callback(50)
                        stages = json.loads(self.observed_job.stages or "[]")
                        subtitle_stage = next(stage for stage in stages if stage.get("key") == "subtitle")
                        self.observed_stage_progress = float(subtitle_stage.get("progress") or 0)
                    return super().burn_subtitles(**kwargs)

            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = ProgressBurnProcessor(temp_dir, job)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=45, platform="youtube", video_id="burn-progress", url=job.source_url, title=job.title)
            db = FakeTaskDb([job], [], [video])
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "burn-progress",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }
            task_ids = iter(range(130, 140))

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
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        processing_preset={},
                        enable_voice=False,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    db,
                    job,
                )

        self.assertTrue(fake_processor.received_progress_callback)
        self.assertEqual(fake_processor.observed_stage_progress, 82.5)
        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["subtitle"].status, "completed")

    def test_resume_reuses_cached_video_when_youtube_parse_requires_auth(self):
        """断点续跑已有下载结果时不应重新解析 YouTube，避免卡在机器人验证"""
        with tempfile.TemporaryDirectory(prefix="automation_resume_cached_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloads", "downloaded.mp4")
            effects_path = os.path.join(temp_dir, "output", "enhanced.mp4")
            os.makedirs(os.path.dirname(downloaded_path), exist_ok=True)
            os.makedirs(os.path.dirname(effects_path), exist_ok=True)
            with open(downloaded_path, "wb") as file:
                file.write(b"downloaded")
            with open(effects_path, "wb") as file:
                file.write(b"effects")

            video = VideoSource(id=42, platform="youtube", video_id="resume-cached", url="https://youtu.be/resume-cached", title="缓存视频")
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            job = AutomationJobRecord(
                id="auto-resume-cached",
                source_url=video.url,
                video_id=42,
                title=video.title,
                status="failed",
                progress=92,
                current_step="流程失败",
                params=json.dumps({
                    "url": video.url,
                    "workspace_dir": temp_dir,
                    "workspace_name": "resume-cached",
                    "video_downloads_dir": os.path.dirname(downloaded_path),
                    "video_output_dir": os.path.dirname(effects_path),
                    "video_exports_dir": os.path.join(temp_dir, "exports"),
                    "output_format": "mp4",
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "parse", "status": "failed", "progress": 5, "task_id": None, "output_path": None, "error_message": "视频解析失败"},
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": downloaded_path, "error_message": None},
                    {"key": "effects", "status": "completed", "progress": 100, "task_id": 2, "output_path": effects_path, "error_message": None},
                    {"key": "subtitle", "status": "skipped", "progress": 100, "task_id": 3, "output_path": None, "error_message": "字幕烧录失败后跳过"},
                    {"key": "voice", "status": "skipped", "progress": 100, "task_id": None, "output_path": None, "error_message": "没有启用或没有已保存配音配置"},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 4, "output_path": effects_path, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [], [video])
            task_ids = iter(range(100, 110))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            _prepare_job_for_resume(job)
            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=FailingParseDownloader()),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation._parse_or_update_video", side_effect=AssertionError("不应重新解析 YouTube")) as parse_mock,
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=True,
                        processing_preset={},
                        enable_voice=False,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    db,
                    job,
                    resume_from_checkpoint=True,
                )

            stage_by_key = {stage.key: stage for stage in response.stages}
            parse_mock.assert_not_called()
            self.assertEqual(stage_by_key["parse"].status, "completed")
            self.assertEqual(stage_by_key["download"].output_path, downloaded_path)
            self.assertEqual(fake_recognizer.video_paths, [effects_path])
            self.assertEqual(fake_processor.burn_calls[0]["video_path"], effects_path)

    def test_resume_rejects_local_timeline_cache_as_video_input(self):
        """继续完成时不能把本地时间轴缓存误当成下载视频传给 ASR"""
        with tempfile.TemporaryDirectory(prefix="automation_resume_timeline_cache_") as temp_dir:
            downloads_dir = os.path.join(temp_dir, "downloads")
            output_dir = os.path.join(temp_dir, "output")
            exports_dir = os.path.join(temp_dir, "exports")
            os.makedirs(downloads_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(exports_dir, exist_ok=True)
            timeline_cache_path = os.path.join(downloads_dir, "video_local_timeline.json")
            downloaded_path = os.path.join(downloads_dir, "downloaded.mp4")
            with open(timeline_cache_path, "w", encoding="utf-8") as file:
                json.dump([{"text": "cached timeline"}], file)
            with open(downloaded_path, "wb") as file:
                file.write(b"downloaded")

            video = VideoSource(id=43, platform="youtube", video_id="resume-timeline", url="https://youtu.be/resume-timeline", title="时间轴缓存")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            job = AutomationJobRecord(
                id="auto-resume-timeline-cache",
                source_url=video.url,
                video_id=43,
                title=video.title,
                status="failed",
                progress=52,
                current_step="流程失败",
                params=json.dumps({
                    "url": video.url,
                    "workspace_dir": temp_dir,
                    "workspace_name": "resume-timeline",
                    "video_downloads_dir": downloads_dir,
                    "video_output_dir": output_dir,
                    "video_exports_dir": exports_dir,
                    "output_format": "mp4",
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "parse", "status": "completed", "progress": 100, "task_id": None, "output_path": None, "error_message": None},
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": timeline_cache_path, "error_message": None},
                    {"key": "effects", "status": "skipped", "progress": 100, "task_id": 2, "output_path": timeline_cache_path, "error_message": None},
                    {"key": "subtitle", "status": "failed", "progress": 22, "task_id": 3, "output_path": None, "error_message": "Invalid data found when processing input"},
                    {"key": "voice", "status": "skipped", "progress": 100, "task_id": None, "output_path": None, "error_message": "没有启用或没有已保存配音配置"},
                    {"key": "export", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [], [video])
            task_ids = iter(range(110, 120))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            _prepare_job_for_resume(job)
            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation._parse_or_update_video", side_effect=AssertionError("不应重新解析 YouTube")),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        processing_preset={},
                        enable_voice=False,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    db,
                    job,
                    resume_from_checkpoint=True,
                )

            stage_by_key = {stage.key: stage for stage in response.stages}
            self.assertEqual(stage_by_key["download"].output_path, downloaded_path)
            self.assertEqual(fake_recognizer.video_paths, [downloaded_path])
            self.assertEqual(fake_processor.burn_calls[0]["video_path"], downloaded_path)
            self.assertNotIn(timeline_cache_path, fake_recognizer.video_paths)

    def test_download_stage_rejects_timeline_meta_json_before_subtitle_stage(self):
        """下载器异常返回本地时间轴元数据时，自动化流程必须在下载阶段拦截"""
        with tempfile.TemporaryDirectory(prefix="automation_download_meta_guard_") as temp_dir:
            downloads_dir = os.path.join(temp_dir, "downloads")
            os.makedirs(downloads_dir, exist_ok=True)
            timeline_meta_path = os.path.join(downloads_dir, "video_local_timeline.meta.json")
            with open(timeline_meta_path, "w", encoding="utf-8") as file:
                json.dump({"segments": []}, file)

            fake_downloader = FakeAutomationDownloader(timeline_meta_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=44, platform="youtube", video_id="download-meta", url="https://youtu.be/download-meta", title="下载元数据误用")
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "download-meta",
                "downloads_dir": downloads_dir,
                "output_dir": os.path.join(temp_dir, "output"),
                "exports_dir": os.path.join(temp_dir, "exports"),
            }
            task_ids = iter(range(120, 130))

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
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _run_automation_sync(
                        AutomationRunRequest(
                            url=video.url,
                            enable_effects=False,
                            processing_preset={},
                            enable_voice=False,
                            burn_subtitles=True,
                            output_format="mp4",
                        ),
                        FakeDb([]),
                    )

        self.assertIn("下载阶段输入不是可用视频文件", str(context.exception))
        self.assertEqual(fake_recognizer.video_paths, [])
        self.assertEqual(fake_processor.burn_calls, [])

    def test_gemini_align_reuses_cached_local_timeline(self):
        """Gemini 内容+本地时间轴继续执行时复用本地时间轴缓存，不再从头跑 ASR"""
        class FakeTimelineRecognizer:
            """测试用本地时间轴识别器"""

            devices: list[str] = []
            model_names: list[str] = []
            transcribe_paths: list[str] = []

            def __init__(self, *_, **kwargs):
                self.devices.append(kwargs.get("device") or "auto")
                self.model_names.append(kwargs.get("model_name") or "auto")

            def transcribe_video(self, video_path, progress_callback=None):
                self.transcribe_paths.append(video_path)
                if progress_callback:
                    progress_callback(100)
                return ([
                    {"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "local timeline"},
                ], "en")

            def _srt_time_to_seconds(self, value):
                text = str(value).replace(",", ".")
                hours, minutes, seconds = text.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

            def _seconds_to_srt_time(self, seconds):
                total_ms = max(0, int(round(float(seconds) * 1000)))
                hours = total_ms // 3600000
                minutes = (total_ms % 3600000) // 60000
                secs = (total_ms % 60000) // 1000
                millis = total_ms % 1000
                return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        class FakeGeminiTranscriber:
            """测试用 Gemini 转写器"""

            languages: list[str | None] = []

            def transcribe_video(self, video_path, language=None, progress_callback=None):
                self.languages.append(language)
                if progress_callback:
                    progress_callback(100)
                return ([
                    {"index": 1, "start": "00:00:00,200", "end": "00:00:03,800", "text": "Gemini content"},
                ], language or "en")

        with tempfile.TemporaryDirectory(prefix="automation_gemini_align_cache_") as temp_dir:
            video_path = os.path.join(temp_dir, "enhanced.mp4")
            with open(video_path, "wb") as file:
                file.write(b"video")
            request = AutomationRunRequest(
                url="https://example.test/video",
                subtitle_recognition_mode="gemini_align",
                enable_effects=False,
                processing_preset={},
                enable_voice=False,
                burn_subtitles=False,
                output_format="mp4",
            )
            fake_gemini = FakeGeminiTranscriber()
            progress_values: list[float] = []

            with (
                patch.dict(os.environ, {
                    "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "auto",
                    "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                    "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
                    "YTV_GEMINI_ALIGN_MIN_FREE_VRAM_MIB": "1800",
                    "YTV_GEMINI_ALIGN_SMALL_VRAM_MIB": "2600",
                }, clear=False),
                patch("backend.api.automation.cuda_device_count", return_value=1),
                patch("backend.api.automation.cuda_free_memory_mib", return_value=3200),
                patch("backend.api.automation.LocalSpeechRecognizer", FakeTimelineRecognizer),
                patch("backend.api.automation._build_gemini_transcriber", return_value=fake_gemini),
            ):
                entries, language = _recognize_subtitle_entries(FakeDb([]), request, video_path, progress_values.append)
                cached_entries, cached_language = _recognize_subtitle_entries(FakeDb([]), request, video_path, progress_values.append)

            cache_path = os.path.join(temp_dir, "enhanced_local_timeline.json")
            cache_exists = os.path.exists(cache_path)

        self.assertEqual(language, "en")
        self.assertEqual(cached_language, "en")
        self.assertEqual([entry["text"] for entry in entries], ["Gemini content"])
        self.assertEqual([entry["text"] for entry in cached_entries], ["Gemini content"])
        self.assertEqual(FakeTimelineRecognizer.devices[0], "cuda")
        self.assertEqual(FakeTimelineRecognizer.model_names[0], "small")
        self.assertEqual(FakeTimelineRecognizer.transcribe_paths, [video_path])
        self.assertEqual(fake_gemini.languages, ["en", "en"])
        self.assertIn(50, progress_values)
        self.assertTrue(cache_exists)

    def test_gemini_align_timeline_profile_uses_safe_gpu_when_vram_is_enough(self):
        """Gemini 对齐时间轴在显存足够时走低 beam GPU，避免长视频固定 CPU 太慢"""
        with (
            patch.dict(os.environ, {
                "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "auto",
                "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
                "YTV_GEMINI_ALIGN_MIN_FREE_VRAM_MIB": "1800",
                "YTV_GEMINI_ALIGN_SMALL_VRAM_MIB": "2600",
            }, clear=False),
            patch("backend.api.automation.cuda_device_count", return_value=1),
            patch("backend.api.automation.cuda_free_memory_mib", return_value=3200),
            patch("backend.api.automation.cuda_memory_mib", return_value=4096),
        ):
            profile = _gemini_align_timeline_profile()

        self.assertEqual(profile, {"device": "cuda", "model_name": "small", "beam_size": "5"})

    def test_gemini_align_timeline_profile_inherits_local_asr_model(self):
        """Gemini 对齐时间轴默认继承本地 ASR 模型，避免用户换 large-v3-turbo 后仍跑 base"""
        with (
            patch.dict(os.environ, {
                "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "",
                "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
                "YTV_ASR_DEVICE": "cuda",
                "YTV_ASR_MODEL": "large-v3-turbo",
            }, clear=False),
            patch("backend.api.automation.asr_cuda_disabled_by_marker", return_value=True),
            patch("backend.api.automation.cuda_device_count", return_value=1),
            patch("backend.api.automation.cuda_free_memory_mib", return_value=6000),
            patch("backend.api.automation.cuda_memory_mib", return_value=8192),
        ):
            profile = _gemini_align_timeline_profile()

        self.assertEqual(profile, {"device": "cuda", "model_name": "large-v3-turbo", "beam_size": "5"})

    def test_gemini_align_timeline_profile_uses_gpu_when_free_vram_query_fails(self):
        """空闲显存读不到但总显存足够时仍走 GPU，避免误报后固定退 CPU"""
        with (
            patch.dict(os.environ, {
                "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "auto",
                "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
                "YTV_ASR_DEVICE": "",
                "YTV_ASR_MODEL": "",
                "YTV_GEMINI_ALIGN_MIN_FREE_VRAM_MIB": "1800",
            }, clear=False),
            patch("backend.api.automation.asr_cuda_disabled_by_marker", return_value=False),
            patch("backend.api.automation.cuda_device_count", return_value=1),
            patch("backend.api.automation.cuda_free_memory_mib", return_value=0),
            patch("backend.api.automation.cuda_memory_mib", return_value=8192),
        ):
            profile = _gemini_align_timeline_profile()

        self.assertEqual(profile, {"device": "cuda", "model_name": "large-v3-turbo", "beam_size": "5"})

    def test_gemini_align_timeline_profile_falls_back_to_cpu_when_vram_is_low(self):
        """Gemini 对齐时间轴在空闲显存不足时退 CPU，避免 CUDA 显存压力导致流程崩溃"""
        with (
            patch.dict(os.environ, {
                "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "auto",
                "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
                "YTV_GEMINI_ALIGN_MIN_FREE_VRAM_MIB": "1800",
                "YTV_GEMINI_ALIGN_SMALL_VRAM_MIB": "2600",
            }, clear=False),
            patch("backend.api.automation.cuda_device_count", return_value=1),
            patch("backend.api.automation.cuda_free_memory_mib", return_value=900),
        ):
            profile = _gemini_align_timeline_profile()

        # 模式3 时间轴默认用 base：内容由 Gemini 出，base 时间骨架够用且更快更省显存
        self.assertEqual(profile, {"device": "cpu", "model_name": "base", "beam_size": "5"})

    def test_gemini_align_timeline_profile_uses_cpu_when_cuda_marker_exists(self):
        """检测到 CUDA 原生崩溃标记时，Gemini 对齐时间轴默认退回 CPU"""
        with (
            patch.dict(os.environ, {
                "YTV_GEMINI_ALIGN_TIMELINE_DEVICE": "auto",
                "YTV_GEMINI_ALIGN_TIMELINE_MODEL": "",
                "YTV_GEMINI_ALIGN_TIMELINE_BEAM_SIZE": "",
            }, clear=False),
            patch("backend.api.automation.asr_cuda_disabled_by_marker", return_value=True),
            patch("backend.api.automation.cuda_device_count", return_value=1),
            patch("backend.api.automation.cuda_free_memory_mib", return_value=6000),
        ):
            profile = _gemini_align_timeline_profile()

        # 模式3 时间轴默认用 base：内容由 Gemini 出，base 时间骨架够用且更快更省显存
        self.assertEqual(profile, {"device": "cpu", "model_name": "base", "beam_size": "5"})

    def test_gemini_align_ignores_old_local_timeline_cache(self):
        """旧版本地时间轴缓存没有配置版本时必须失效，避免继续复用低精度时间轴"""
        class FreshTimelineRecognizer:
            """测试用新时间轴识别器"""

            transcribe_paths: list[str] = []

            def __init__(self, *_, **__):
                pass

            def transcribe_video(self, video_path, progress_callback=None):
                self.transcribe_paths.append(video_path)
                if progress_callback:
                    progress_callback(100)
                return ([
                    {"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "fresh timeline"},
                ], "en")

            def _srt_time_to_seconds(self, value):
                text = str(value).replace(",", ".")
                hours, minutes, seconds = text.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

            def _seconds_to_srt_time(self, seconds):
                total_ms = max(0, int(round(float(seconds) * 1000)))
                hours = total_ms // 3600000
                minutes = (total_ms % 3600000) // 60000
                secs = (total_ms % 60000) // 1000
                millis = total_ms % 1000
                return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        class FakeGeminiTranscriber:
            """测试用 Gemini 转写器"""

            def transcribe_video(self, video_path, language=None, progress_callback=None):
                if progress_callback:
                    progress_callback(100)
                return ([
                    {"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "Gemini fresh"},
                ], language or "en")

        with tempfile.TemporaryDirectory(prefix="automation_old_gemini_align_cache_") as temp_dir:
            video_path = os.path.join(temp_dir, "enhanced.mp4")
            with open(video_path, "wb") as file:
                file.write(b"video")
            cache_path = os.path.join(temp_dir, "enhanced_local_timeline.json")
            meta_path = os.path.join(temp_dir, "enhanced_local_timeline.meta.json")
            signature = {"size": os.path.getsize(video_path), "mtime": os.path.getmtime(video_path)}
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"entries": [{"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "stale timeline"}]}, file)
            with open(meta_path, "w", encoding="utf-8") as file:
                json.dump({"video_path": os.path.abspath(video_path), "signature": signature, "language": "en"}, file)
            request = AutomationRunRequest(
                url="https://example.test/video",
                subtitle_recognition_mode="gemini_align",
                enable_effects=False,
                processing_preset={},
                enable_voice=False,
                burn_subtitles=False,
                output_format="mp4",
            )

            with (
                patch("backend.api.automation.LocalSpeechRecognizer", FreshTimelineRecognizer),
                patch("backend.api.automation._build_gemini_transcriber", return_value=FakeGeminiTranscriber()),
            ):
                entries, _language = _recognize_subtitle_entries(FakeDb([]), request, video_path, lambda _value: None)

        self.assertEqual(FreshTimelineRecognizer.transcribe_paths, [video_path])
        self.assertEqual([entry["text"] for entry in entries], ["Gemini fresh"])

    def test_gemini_align_reuses_timeline_cache_after_cuda_fallback(self):
        """CUDA 熔断切到 CPU 后仍复用同模型时间轴缓存，不应因为执行设备变化从头识别"""
        with tempfile.TemporaryDirectory(prefix="automation_cuda_cache_reuse_") as temp_dir:
            video_path = os.path.join(temp_dir, "enhanced.mp4")
            with open(video_path, "wb") as file:
                file.write(b"video")
            cache_path = os.path.join(temp_dir, "enhanced_local_timeline.json")
            meta_path = os.path.join(temp_dir, "enhanced_local_timeline.meta.json")
            signature = {"size": os.path.getsize(video_path), "mtime": os.path.getmtime(video_path)}
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"entries": [{"index": 1, "start": "00:00:00,000", "end": "00:00:04,000", "text": "cached timeline"}]}, file)
            with open(meta_path, "w", encoding="utf-8") as file:
                json.dump({
                    "cache_version": 2,
                    "video_path": os.path.abspath(video_path),
                    "signature": signature,
                    "timeline_profile": {"device": "cuda", "model_name": "base", "beam_size": "2"},
                    "language": "en",
                }, file)

            with (
                patch("backend.api.automation.asr_cuda_disabled_by_marker", return_value=True),
                patch("backend.api.automation.cuda_device_count", return_value=1),
                patch("backend.api.automation.cuda_free_memory_mib", return_value=6000),
            ):
                cached = _load_gemini_align_timeline_cache(video_path)

        self.assertIsNotNone(cached)
        entries, language = cached or ([], "")
        self.assertEqual(language, "en")
        self.assertEqual(entries[0]["text"], "cached timeline")

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

    def test_automation_voice_uses_translated_chinese_timeline(self):
        """翻译后配音必须按最终中文字幕时间轴生成，不能读原文或旧整段文案"""
        class EnglishRecognizer:
            """测试用英文识别器"""

            def transcribe_video(self, video_path, progress_callback=None):
                if progress_callback:
                    progress_callback(100)
                return ([
                    {"index": 1, "start": "00:00:00,000", "end": "00:00:01,200", "text": "hello world"},
                    {"index": 2, "start": "00:00:01,200", "end": "00:00:02,500", "text": "open the door"},
                ], "en")

        class FakeTextEngine:
            """测试用文本引擎，模拟英文翻译成中文"""

            async def process_subtitle_entries(self, entries, **_kwargs):
                translations = ["你好世界", "打开那扇门"]
                return [{**entry, "text": translations[index]} for index, entry in enumerate(entries)]

        class CapturingVoiceEngine:
            """记录传入配音接口的时间轴分段"""

            def __init__(self):
                self.segments: list[dict] = []

            async def generate_batched_timed_voice_track(self, segments, output_path, progress_callback=None, **_kwargs):
                """模拟按中文字幕时间轴配音"""
                self.segments = segments
                if progress_callback:
                    progress_callback(100)
                with open(output_path, "wb") as file:
                    file.write(b"voice")
                return output_path

            async def generate_voice(self, *_args, **_kwargs):
                """有字幕时间轴时不应回退整段配音"""
                raise AssertionError("翻译字幕可用时不应走整段配音")

        with tempfile.TemporaryDirectory(prefix="automation_voice_translate_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="voice-translate", url="https://example.test/video", title="Voice Translate")
            text_profile = TextProviderProfile(id=21, name="文本", provider_type="openai_compatible", base_url="https://example.test/v1", api_key_encrypted="encrypted", model="test-model")
            voice_profile = VoiceProviderProfile(id=22, name="配音", provider_type="openai_tts", base_url="https://example.test/v1", api_key_encrypted="encrypted", voice="voice-model")
            task_ids = iter(range(200, 220))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-translate",
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
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=text_profile),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        enable_voice=True,
                        subtitle_operation="translate",
                        subtitle_target_language="zh-CN",
                        text_profile_id=21,
                        voice_profile_id=22,
                        voice_text="这段旧文案不应该参与时间轴配音",
                        voice_mode="batched",
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        self.assertEqual([segment["text"] for segment in voice_engine.segments], ["你好世界", "打开那扇门"])
        self.assertEqual([segment["start_ms"] for segment in voice_engine.segments], [0, 1200])
        self.assertNotIn("hello world", [segment["text"] for segment in voice_engine.segments])
        self.assertNotIn("这段旧文案不应该参与时间轴配音", [segment["text"] for segment in voice_engine.segments])

    def test_automation_translation_failure_stops_for_resume(self):
        """字幕翻译重试耗尽后停在字幕阶段，不应静默跳过并继续导出"""
        with tempfile.TemporaryDirectory(prefix="automation_translate_fail_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            video = VideoSource(id=1, platform="youtube", video_id="translate-fail", url="https://example.test/video", title="Translate Fail")
            text_profile = TextProviderProfile(
                id=12,
                name="失败文本",
                provider_type="openai_compatible",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                model="test-model",
            )
            job = AutomationJobRecord(
                id="auto-translate-fail",
                source_url=video.url,
                title=video.title,
                status="running",
                stages=json.dumps(_default_stages(), ensure_ascii=False),
            )
            db = FakeTaskDb([job], [])
            task_ids = iter(range(30, 40))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "translate-fail",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FakeAutomationRecognizer()),
                patch("backend.api.automation.TextEngine", return_value=FailingTextEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=text_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _run_automation_sync(
                        AutomationRunRequest(
                            url=video.url,
                            enable_effects=False,
                            processing_preset={},
                            enable_voice=False,
                            burn_subtitles=True,
                            subtitle_operation="translate",
                            subtitle_target_language="zh-CN",
                            text_profile_id=12,
                            output_format="mp4",
                        ),
                        db,
                        job,
                    )

            stages = {stage["key"]: stage for stage in json.loads(job.stages)}
            self.assertIn("继续完成", str(context.exception))
            self.assertEqual(stages["subtitle"]["status"], "failed")
            self.assertIn("字幕翻译失败", stages["subtitle"]["error_message"])
            self.assertEqual(stages["export"]["status"], "pending")
            self.assertEqual(fake_processor.convert_calls, [])

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
                {"key": "subtitle", "status": "skipped", "progress": 100, "task_id": 5, "output_path": None, "error_message": "字幕烧录失败后跳过"},
                {"key": "parse", "status": "cancelled", "progress": 10, "task_id": 6, "output_path": None, "error_message": "取消"},
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
        self.assertEqual(stages["parse"]["status"], "pending")

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
        self.assertEqual(job.output_path, "D:/old.mp4")
        self.assertEqual(stages["download"]["status"], "completed")
        self.assertEqual(stages["subtitle"]["status"], "completed")
        self.assertEqual(stages["export"]["status"], "pending")
        self.assertEqual(stages["export"]["output_path"], "D:/old.mp4")

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

    def test_prepare_interrupted_job_for_startup_recovers_old_cancelled_restart_message(self):
        """旧版本写入的后端重启取消记录，应自动恢复为可续跑任务"""
        job = AutomationJobRecord(
            id="auto-startup-cancelled",
            source_url="https://youtube.com/watch?v=test",
            status="cancelled",
            progress=45,
            current_step="已取消",
            error_message=BACKEND_RESTART_INTERRUPTED_MESSAGE,
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
                {"key": "subtitle", "status": "cancelled", "progress": 45, "task_id": 2, "output_path": None, "error_message": BACKEND_RESTART_INTERRUPTED_MESSAGE},
                {"key": "export", "status": "cancelled", "progress": 0, "task_id": None, "output_path": None, "error_message": BACKEND_RESTART_INTERRUPTED_MESSAGE},
            ], ensure_ascii=False),
        )

        _prepare_interrupted_job_for_startup(job)
        stages = {stage["key"]: stage for stage in json.loads(job.stages)}

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.current_step, "后端重启后等待恢复")
        self.assertIsNone(job.error_message)
        self.assertEqual(stages["download"]["status"], "completed")
        self.assertEqual(stages["subtitle"]["status"], "pending")
        self.assertEqual(stages["export"]["status"], "pending")
        self.assertIsNone(stages["subtitle"]["error_message"])

    def test_recover_automation_jobs_on_startup_resubmits_interrupted_jobs(self):
        """后端启动时不再把运行中任务取消，而是提交断点续跑"""
        running_job = AutomationJobRecord(
            id="auto-running-startup",
            source_url="https://youtube.com/watch?v=test",
            status="running",
            progress=60,
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/download.mp4", "error_message": None},
                {"key": "subtitle", "status": "running", "progress": 60, "task_id": 2, "output_path": None, "error_message": None},
            ], ensure_ascii=False),
        )
        old_cancelled_job = AutomationJobRecord(
            id="auto-old-cancelled-startup",
            source_url="https://youtube.com/watch?v=test2",
            status="cancelled",
            error_message=BACKEND_RESTART_INTERRUPTED_MESSAGE,
            stages=json.dumps([
                {"key": "parse", "status": "cancelled", "progress": 10, "task_id": None, "output_path": None, "error_message": BACKEND_RESTART_INTERRUPTED_MESSAGE},
            ], ensure_ascii=False),
        )
        user_cancelled_job = AutomationJobRecord(
            id="auto-user-cancelled-startup",
            source_url="https://youtube.com/watch?v=test3",
            status="cancelled",
            error_message="用户取消",
            stages=json.dumps(_default_stages(), ensure_ascii=False),
        )
        db = FakeDb([running_job, old_cancelled_job, user_cancelled_job])
        submitted_ids: list[str] = []

        with patch("backend.api.automation.SessionLocal", return_value=db), \
                patch("backend.api.automation._submit_automation_job", side_effect=lambda job_id, resume_from_checkpoint=False: submitted_ids.append(job_id)):
            result = recover_automation_jobs_on_startup()

        self.assertEqual(result, {"submitted": 2, "paused": 0, "interrupted": 2})
        self.assertEqual(submitted_ids, ["auto-running-startup", "auto-old-cancelled-startup"])
        self.assertEqual(running_job.status, "pending")
        self.assertEqual(old_cancelled_job.status, "pending")
        self.assertEqual(user_cancelled_job.status, "cancelled")
        self.assertEqual(db.commit_count, 1)

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

    def test_validate_automation_skip_subtitle_does_not_require_text_profile(self):
        """完全不处理字幕时，即使识别方式保留 Gemini，也不应要求文本 API 配置"""
        validate_automation_request_profiles(
            FakeDb([]),
            AutomationRunRequest(
                url="https://youtube.com/watch?v=test",
                subtitle_operation="skip",
                subtitle_recognition_mode="gemini_align",
                burn_subtitles=True,
                enable_voice=False,
            ),
        )

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

    def test_sync_subtitle_entries_to_voice_timeline_extends_tail_without_overlap(self):
        """最终烧录字幕按配音真实尾音延长，但不会盖到下一条字幕"""
        entries = [
            {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "第一句"},
            {"index": 2, "start": "00:00:02,800", "end": "00:00:04,000", "text": "第二句"},
        ]
        voice_timeline = [
            {"start_ms": 1000, "duration_ms": 1000, "source_duration_ms": 1500, "audio_end_ms": 2500},
            {"start_ms": 2800, "duration_ms": 1200, "source_duration_ms": 1000, "audio_end_ms": 3800},
        ]

        synced = _sync_subtitle_entries_to_voice_timeline(entries, voice_timeline)

        self.assertEqual(synced[0]["end"], "00:00:02,620")
        self.assertEqual(synced[1]["end"], "00:00:04,000")

    def test_sync_subtitle_entries_to_voice_timeline_follows_delayed_voice_start(self):
        """配音避让导致下一句顺延时，最终字幕开始时间也跟随配音"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:00,500", "text": "第一句"},
            {"index": 2, "start": "00:00:00,600", "end": "00:00:01,100", "text": "第二句"},
        ]
        voice_timeline = [
            {"start_ms": 0, "original_start_ms": 0, "duration_ms": 500, "source_duration_ms": 900, "audio_end_ms": 900},
            {"start_ms": 1200, "original_start_ms": 600, "duration_ms": 500, "source_duration_ms": 300, "audio_end_ms": 1500},
        ]

        synced = _sync_subtitle_entries_to_voice_timeline(entries, voice_timeline)

        self.assertEqual(synced[1]["start"], "00:00:01,200")
        self.assertEqual(synced[1]["end"], "00:00:01,620")

    def test_sync_subtitle_entries_to_voice_timeline_allows_long_delayed_tail(self):
        """配音明显顺延时，字幕结束时间跟随真实尾音，不再被原字幕窗口硬限制"""
        entries = [
            {"index": 1, "start": "00:00:00,000", "end": "00:00:00,500", "text": "第一句"},
            {"index": 2, "start": "00:00:00,600", "end": "00:00:01,000", "text": "第二句"},
        ]
        voice_timeline = [
            {"start_ms": 0, "original_start_ms": 0, "duration_ms": 500, "source_duration_ms": 2200, "audio_end_ms": 2200},
            {"start_ms": 2320, "original_start_ms": 600, "duration_ms": 400, "source_duration_ms": 800, "audio_end_ms": 3120},
        ]

        synced = _sync_subtitle_entries_to_voice_timeline(entries, voice_timeline)

        self.assertEqual(synced[0]["end"], "00:00:02,260")
        self.assertEqual(synced[1]["start"], "00:00:02,320")
        self.assertEqual(synced[1]["end"], "00:00:03,240")

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

    def test_combine_original_and_translated_entries_uses_source_index_after_split(self):
        """一条原文拆成多条译文时，双语对照仍应指向同一条原文"""
        original = [
            {"index": 11, "start": "00:00:19,100", "end": "00:00:22,380", "text": "I joined in with a plan to make the best possible base all while keeping it completely hidden from the rest of the server"},
            {"index": 12, "start": "00:00:22,380", "end": "00:00:25,300", "text": "And then I needed to move villagers"},
        ]
        translated = [
            {"index": 1, "source_index": 11, "start": "00:00:19,100", "end": "00:00:20,690", "text": "我加入并制定了一个计划"},
            {"index": 2, "source_index": 11, "start": "00:00:20,690", "end": "00:00:22,380", "text": "要建造一个最好的基地"},
        ]

        combined = combine_original_and_translated_entries(original, translated)

        self.assertEqual(len(combined), 2)
        self.assertEqual(combined[0]["text"], f"{translated[0]['text']}\n{original[0]['text']}")
        self.assertEqual(combined[1]["text"], f"{translated[1]['text']}\n{original[0]['text']}")
        self.assertNotIn("villagers", combined[1]["text"])

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
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "export-render",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation.ensure_project_dirs", return_value={"output_dir": temp_dir, "exports_dir": temp_dir}),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
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

    def test_original_subtitle_without_burn_skips_subtitle_stage_when_voice_disabled(self):
        """使用原字幕且不烧录、不配音时，一键流程应直接跳过字幕加工"""
        with tempfile.TemporaryDirectory(prefix="automation_original_skip_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            video = VideoSource(id=1, platform="youtube", video_id="original-skip", url="https://example.test/video", title="原字幕跳过")
            task_ids = iter(range(30, 40))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "original-skip",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }

            class FailingRecognizer:
                """原字幕跳过模式不应启动本地识别"""

                def transcribe_video(self, *_args, **_kwargs):
                    raise AssertionError("原字幕不烧录且未配音时不应识别字幕")

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FailingRecognizer()),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", side_effect=AssertionError("原字幕不烧录时不应校验文本 API")),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        subtitle_operation="none",
                        subtitle_recognition_mode="gemini_align",
                        enable_voice=False,
                        burn_subtitles=False,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["subtitle"].status, "skipped")
        self.assertIn("跳过字幕加工", stage_by_key["subtitle"].error_message)
        self.assertEqual(fake_downloader.subtitle_download_calls, 0)
        self.assertEqual(fake_processor.burn_calls, [])
        self.assertEqual(fake_processor.merge_calls, [])

    def test_original_subtitle_without_burn_uses_timeline_only_for_smart_voice(self):
        """使用原字幕且不烧录、开启配音时，只读取原字幕时间轴给智能配音使用"""
        class CapturingVoiceEngine:
            """记录分段配音输入，避免真实调用配音接口"""

            def __init__(self):
                self.segments: list[dict] = []

            async def generate_batched_timed_voice_track(self, segments, output_path, progress_callback=None, **_kwargs):
                """模拟按原字幕时间轴生成智能配音"""
                self.segments = segments
                if progress_callback:
                    progress_callback(100)
                with open(output_path, "wb") as file:
                    file.write(b"voice")
                return output_path

            async def generate_timed_voice_track(self, *_args, **_kwargs):
                """旧逐句模式不应再被一键流程调用"""
                raise AssertionError("一键配音应统一走智能时间轴")

            async def generate_voice(self, *_args, **_kwargs):
                """原字幕时间轴可用时不应回退整段配音"""
                raise AssertionError("原字幕时间轴可用时不应回退整段配音")

        with tempfile.TemporaryDirectory(prefix="automation_original_voice_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeOriginalSubtitleDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="original-voice", url="https://example.test/video", title="原字幕配音")
            voice_profile = VoiceProviderProfile(
                id=4,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            task_ids = iter(range(60, 75))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "original-voice",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }

            class FailingRecognizer:
                """原字幕时间轴可用时不应启动本地识别"""

                def transcribe_video(self, *_args, **_kwargs):
                    raise AssertionError("原字幕时间轴可用时不应识别字幕")

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FailingRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", side_effect=AssertionError("原字幕时间轴模式不应调用文本 API")),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        subtitle_operation="none",
                        subtitle_language="en",
                        enable_voice=True,
                        burn_subtitles=False,
                        voice_mode="segmented",
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["subtitle"].status, "skipped")
        self.assertIn("原字幕时间轴", stage_by_key["subtitle"].error_message)
        self.assertEqual(fake_downloader.subtitle_download_calls, 1)
        self.assertEqual(fake_processor.burn_calls, [])
        self.assertEqual(fake_processor.merge_calls[0]["video_path"], downloaded_path)
        self.assertEqual(voice_engine.segments[0]["text"], "原字幕第一句")
        self.assertIn("原字幕第一句", response.subtitle_text)

    def test_skip_subtitle_stage_ignores_burn_and_skips_voice(self):
        """不处理字幕时，即使开启烧录和配音，也不应识别、读取、烧录字幕或生成配音"""
        class CapturingVoiceEngine:
            """确认完全跳过字幕时不会生成配音"""

            def __init__(self):
                self.called = False

            async def generate_batched_timed_voice_track(self, *_args, **_kwargs):
                """不处理字幕时不应进入智能时间轴配音"""
                self.called = True
                raise AssertionError("不处理字幕时不应使用字幕时间轴配音")

            async def generate_timed_voice_track(self, *_args, **_kwargs):
                """不处理字幕时不应进入分段配音"""
                self.called = True
                raise AssertionError("不处理字幕时不应使用字幕时间轴分段配音")

            async def generate_voice(self, text, output_path, **_kwargs):
                """模拟整段配音并记录文案"""
                self.called = True
                raise AssertionError("不处理字幕时不应整段配音")

        with tempfile.TemporaryDirectory(prefix="automation_skip_subtitle_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="skip-subtitle", url="https://example.test/video", title="跳过字幕测试")
            voice_profile = VoiceProviderProfile(
                id=5,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            task_ids = iter(range(80, 95))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "skip-subtitle",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }

            class FailingRecognizer:
                """完全跳过字幕时不应启动本地识别"""

                def transcribe_video(self, *_args, **_kwargs):
                    raise AssertionError("不处理字幕时不应识别字幕")

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FailingRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", side_effect=AssertionError("不处理字幕时不应读取字幕预设")),
                patch("backend.api.automation._pick_text_profile", side_effect=AssertionError("不处理字幕时不应调用文本 API")),
                patch("backend.api.automation._pick_voice_profile", side_effect=AssertionError("不处理字幕时不应读取配音配置")),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        subtitle_operation="skip",
                        subtitle_recognition_mode="gemini_align",
                        enable_voice=True,
                        burn_subtitles=True,
                        voice_mode="segmented",
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["subtitle"].status, "skipped")
        self.assertIn("不处理字幕", stage_by_key["subtitle"].error_message)
        self.assertEqual(stage_by_key["voice"].status, "skipped")
        self.assertIn("无法按中文字幕时间轴配音", stage_by_key["voice"].error_message)
        self.assertEqual(fake_downloader.subtitle_download_calls, 0)
        self.assertEqual(fake_processor.burn_calls, [])
        self.assertEqual(fake_processor.merge_calls, [])
        self.assertFalse(voice_engine.called)
        self.assertEqual(response.subtitle_text, "")

    def test_automation_voice_can_export_subtitle_only_copy(self):
        """开启配音时可额外导出一份只有字幕、没有配音的视频"""
        with tempfile.TemporaryDirectory(prefix="automation_subtitle_only_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=1, platform="youtube", video_id="voice-copy", url="https://example.test/video", title="配音字幕版")
            voice_profile = VoiceProviderProfile(
                id=2,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            task_ids = iter(range(40, 50))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-copy",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation.VoiceEngine", return_value=FakeVoiceEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        enable_voice=True,
                        export_subtitle_only_when_voice=True,
                        burn_subtitles=True,
                        output_format="mp4",
                    ),
                    FakeDb([]),
                )

            self.assertTrue(response.subtitle_only_video_path)
            self.assertTrue(response.subtitle_only_video_path.endswith("_subtitle_only.mp4"))
            self.assertTrue(os.path.isfile(response.subtitle_only_video_path))
            self.assertEqual(len(fake_processor.convert_calls), 2)
            self.assertEqual(fake_processor.convert_calls[0]["output_path"], response.subtitle_only_video_path)
            self.assertNotIn("output_path", fake_processor.convert_calls[1])
            self.assertEqual(fake_processor.merge_calls[0]["video_path"], downloaded_path)
            self.assertEqual(len(fake_processor.burn_calls), 2)
            self.assertEqual(fake_processor.burn_calls[0]["video_path"], downloaded_path)
            self.assertEqual(fake_processor.burn_calls[1]["video_path"], os.path.join(temp_dir, "merged.mp4"))

    def test_automation_voice_uses_batched_timeline_by_default(self):
        """一键配音默认使用严格时间轴逐条并发，避免分组配音和逐行字幕错位"""
        class CapturingVoiceEngine:
            """记录默认批量配音输入，避免真实调用配音接口"""

            def __init__(self):
                self.segments: list[dict] = []
                self.settings: dict = {}
                self.voices: list[str] = []
                self.styles: list[str] = []

            async def generate_grouped_timed_voice_track(self, *_args, **_kwargs):
                """默认严格时间轴不应调用自然分组接口"""
                raise AssertionError("默认严格时间轴不应调用自然分组接口")

            async def generate_batched_timed_voice_track(self, segments, output_path, voice_selector=None, style_selector=None, settings=None, progress_callback=None, **_kwargs):
                """模拟默认时间轴批量配音"""
                self.segments = segments
                self.settings = settings or {}
                if voice_selector:
                    self.voices = [voice_selector(segment) for segment in segments]
                if style_selector:
                    self.styles = [style_selector(segment) for segment in segments]
                if progress_callback:
                    progress_callback(100)
                with open(output_path, "wb") as file:
                    file.write(b"voice")
                return output_path

            async def generate_timed_voice_track(self, *_args, **_kwargs):
                """默认严格时间轴不应退回串行逐句接口"""
                raise AssertionError("默认严格时间轴不应调用串行逐句接口")

            async def generate_voice(self, *_args, **_kwargs):
                """字幕时间轴可用时不应退回整段配音"""
                raise AssertionError("时间轴配音不应调用整段接口")

        with tempfile.TemporaryDirectory(prefix="automation_voice_batched_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="voice-batched-default", url="https://example.test/video", title="默认批量配音")
            voice_profile = VoiceProviderProfile(
                id=6,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
                extra_params=json.dumps({"speed": 1.8}, ensure_ascii=False),
            )
            task_ids = iter(range(95, 110))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-batched-default",
                "downloads_dir": temp_dir,
                "output_dir": temp_dir,
                "exports_dir": temp_dir,
            }

            class MultiSpeakerRecognizer:
                """返回带说话人标签的字幕，验证自动多人音色选择"""

                def transcribe_video(self, video_path=None, progress_callback=None, **_kwargs):
                    _ = video_path
                    if progress_callback:
                        progress_callback(100)
                    return ([
                        {"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "旁白：第一句"},
                        {"index": 2, "start": "00:00:01,000", "end": "00:00:02,000", "text": "角色A：第二句"},
                    ], "zh")

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                """创建测试任务对象"""
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.Downloader", return_value=fake_downloader),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=MultiSpeakerRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        enable_voice=True,
                        burn_subtitles=True,
                        output_format="mp4",
                        multi_speaker_enabled=True,
                        speaker_voice_map={"旁白": "alloy", "角色 A": "nova"},
                        speaker_voice_styles={"旁白": "解说风格", "角色 A": "对话风格"},
                        voice_batch_size=5,
                        voice_batch_chars=900,
                        voice_concurrency=3,
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["voice"].status, "completed")
        self.assertEqual([segment["text"] for segment in voice_engine.segments], ["第一句", "第二句"])
        self.assertEqual(voice_engine.voices, ["alloy", "nova"])
        self.assertEqual(voice_engine.styles, ["解说风格", "对话风格"])
        self.assertEqual(voice_engine.settings["voice_batch_size"], 16)
        self.assertEqual(voice_engine.settings["voice_batch_chars"], 1800)
        self.assertEqual(voice_engine.settings["voice_concurrency"], 3)
        self.assertEqual(voice_engine.settings["speed"], 1.0)
        self.assertEqual(fake_processor.merge_calls[0]["mode"], "mix")
        self.assertEqual(fake_processor.merge_calls[0]["volume_ratio"], 0.25)

    def test_automation_voice_legacy_grouped_mode_uses_smart_timeline(self):
        """旧分组参数会被兼容接收，但一键配音统一走智能时间轴"""
        class CapturingGroupedVoiceEngine:
            """记录分组配音输入，避免真实调用配音接口"""

            def __init__(self):
                self.segments: list[dict] = []
                self.settings: dict = {}

            async def generate_grouped_timed_voice_track(self, segments, output_path, settings=None, progress_callback=None, **_kwargs):
                """旧分组模式不应再被一键流程调用"""
                raise AssertionError("旧分组模式应迁移到智能时间轴")

            async def generate_batched_timed_voice_track(self, *_args, **_kwargs):
                """模拟智能时间轴配音"""
                segments = _kwargs.get("segments") if "segments" in _kwargs else _args[0]
                output_path = _kwargs.get("output_path") if "output_path" in _kwargs else _args[1]
                self.segments = segments
                self.settings = _kwargs.get("settings") or {}
                progress_callback = _kwargs.get("progress_callback")
                if progress_callback:
                    progress_callback(100)
                with open(output_path, "wb") as file:
                    file.write(b"voice")
                return output_path

            async def generate_timed_voice_track(self, *_args, **_kwargs):
                """分组模式不应调用串行逐句接口"""
                raise AssertionError("分组模式不应调用串行逐句接口")

            async def generate_voice(self, *_args, **_kwargs):
                """分组模式有时间轴时不应调用整段配音"""
                raise AssertionError("分组模式不应调用整段接口")

        with tempfile.TemporaryDirectory(prefix="automation_voice_grouped_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingGroupedVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="voice-grouped", url="https://example.test/video", title="分组配音")
            voice_profile = VoiceProviderProfile(
                id=16,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            task_ids = iter(range(195, 210))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-grouped",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FakeAutomationRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                response = _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        enable_voice=True,
                        burn_subtitles=True,
                        output_format="mp4",
                        voice_mode="grouped",
                        voice_group_size=5,
                        voice_group_chars=360,
                        voice_group_max_seconds=9,
                        voice_group_gap_ms=600,
                        voice_concurrency=3,
                    ),
                    FakeDb([]),
                )

        stage_by_key = {stage.key: stage for stage in response.stages}
        self.assertEqual(stage_by_key["voice"].status, "completed")
        self.assertEqual([segment["text"] for segment in voice_engine.segments], ["本地识别字幕"])
        self.assertNotIn("voice_group_size", voice_engine.settings)
        self.assertNotIn("voice_group_chars", voice_engine.settings)
        self.assertNotIn("voice_group_max_seconds", voice_engine.settings)
        self.assertNotIn("voice_group_gap_ms", voice_engine.settings)
        self.assertEqual(voice_engine.settings["voice_batch_size"], 16)
        self.assertEqual(voice_engine.settings["voice_batch_chars"], 1800)
        self.assertEqual(voice_engine.settings["voice_concurrency"], 3)

    def test_automation_voice_invalid_audio_mode_falls_back_to_mix(self):
        """一键配音遇到异常音频模式时默认混合原声，避免误静音 BGM 和游戏声音"""
        with tempfile.TemporaryDirectory(prefix="automation_voice_audio_mode_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            fake_recognizer = FakeAutomationRecognizer()
            video = VideoSource(id=1, platform="youtube", video_id="voice-audio-mode", url="https://example.test/video", title="配音混音")
            voice_profile = VoiceProviderProfile(
                id=8,
                name="配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            task_ids = iter(range(130, 145))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-audio-mode",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=fake_recognizer),
                patch("backend.api.automation.VoiceEngine", return_value=FakeVoiceEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                _run_automation_sync(
                    AutomationRunRequest(
                        url=video.url,
                        enable_effects=False,
                        enable_voice=True,
                        burn_subtitles=True,
                        output_format="mp4",
                        audio_mode="",
                        original_volume=2,
                    ),
                    FakeDb([]),
                )

        self.assertEqual(fake_processor.merge_calls[0]["mode"], "mix")
        self.assertEqual(fake_processor.merge_calls[0]["volume_ratio"], 1.0)

    def test_background_audio_mode_keeps_ai_background_at_full_volume(self):
        """AI 去人声模式混的是 no_vocals 背景轨，不再复用原声音量把背景压低"""
        self.assertEqual(_audio_merge_volume("background", 0.25), 1.0)
        self.assertEqual(_audio_merge_volume("background", 0), 1.0)
        self.assertEqual(_audio_merge_volume("mix", 0.25), 0.25)

    def test_automation_voice_failure_stops_for_resume(self):
        """配音重试耗尽后停在配音阶段，不应导出无配音视频"""
        with tempfile.TemporaryDirectory(prefix="automation_voice_fail_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            video = VideoSource(id=1, platform="youtube", video_id="voice-fail", url="https://example.test/video", title="配音失败")
            voice_profile = VoiceProviderProfile(
                id=3,
                name="失败配音",
                provider_type="openai_tts",
                base_url="https://example.test/v1",
                api_key_encrypted="encrypted",
                voice="voice-model",
            )
            job = AutomationJobRecord(
                id="auto-voice-fail",
                source_url=video.url,
                title=video.title,
                status="running",
                stages=json.dumps(_default_stages(), ensure_ascii=False),
            )
            db = FakeTaskDb([job], [])
            task_ids = iter(range(50, 60))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-fail",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FakeAutomationRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=FailingVoiceEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _run_automation_sync(
                        AutomationRunRequest(
                            url=video.url,
                            enable_effects=False,
                            processing_preset={},
                            enable_voice=True,
                            burn_subtitles=True,
                            output_format="mp4",
                        ),
                        db,
                        job,
                    )

            stages = {stage["key"]: stage for stage in json.loads(job.stages)}
            self.assertIn("继续完成", str(context.exception))
            self.assertEqual(stages["voice"]["status"], "failed")
            self.assertIn("配音生成失败", stages["voice"]["error_message"])
            self.assertEqual(stages["export"]["status"], "pending")
            self.assertEqual(fake_processor.convert_calls, [])
            self.assertEqual(fake_processor.merge_calls, [])

    def test_timeline_voice_failure_does_not_fallback_to_full_voice(self):
        """按字幕时间轴配音失败时必须停下，不能回退整段配音生成错位音轨"""
        with tempfile.TemporaryDirectory(prefix="automation_voice_no_fallback_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            video = VideoSource(id=1, platform="youtube", video_id="voice-no-fallback", url="https://example.test/video", title="配音不回退")
            voice_profile = VoiceProviderProfile(id=23, name="配音", provider_type="openai_tts", base_url="https://example.test/v1", api_key_encrypted="encrypted", voice="voice-model")
            task_ids = iter(range(220, 240))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "voice-no-fallback",
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
                patch("backend.api.automation.LocalSpeechRecognizer", return_value=FakeAutomationRecognizer()),
                patch("backend.api.automation.VoiceEngine", return_value=FailingSegmentVoiceEngine()),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_subtitle_preset", return_value=None),
                patch("backend.api.automation._pick_text_profile", return_value=None),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _run_automation_sync(
                        AutomationRunRequest(
                            url=video.url,
                            enable_effects=False,
                            enable_voice=True,
                            burn_subtitles=True,
                            voice_mode="batched",
                            output_format="mp4",
                        ),
                        FakeDb([]),
                    )

        self.assertIn("避免回退整段配音导致音画错位", str(context.exception))
        self.assertEqual(fake_processor.merge_calls, [])
        self.assertEqual(fake_processor.convert_calls, [])

    def test_resume_restores_voice_timeline_from_completed_subtitle_file(self):
        """断点续跑复用字幕阶段时，也要从字幕文件恢复配音时间轴"""
        class CapturingVoiceEngine:
            """记录续跑时传入的配音分段"""

            def __init__(self):
                self.segments: list[dict] = []

            async def generate_batched_timed_voice_track(self, segments, output_path, progress_callback=None, **_kwargs):
                """模拟按恢复出的字幕时间轴配音"""
                self.segments = segments
                if progress_callback:
                    progress_callback(100)
                with open(output_path, "wb") as file:
                    file.write(b"voice")
                return output_path

            async def generate_voice(self, *_args, **_kwargs):
                """有字幕文件时不应回退整段配音"""
                raise AssertionError("断点续跑有字幕文件时不应整段配音")

        with tempfile.TemporaryDirectory(prefix="automation_resume_voice_timeline_") as temp_dir:
            downloaded_path = os.path.join(temp_dir, "downloaded.mp4")
            subtitle_path = os.path.join(temp_dir, "downloaded_zh.ass")
            with open(downloaded_path, "wb") as file:
                file.write(b"video")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.10,Default,,0,0,0,,续跑第一句
Dialogue: 0,0:00:01.10,0:00:02.40,Default,,0,0,0,,续跑第二句
""")
            fake_downloader = FakeAutomationDownloader(downloaded_path)
            fake_processor = FakeAutomationProcessor(temp_dir)
            voice_engine = CapturingVoiceEngine()
            video = VideoSource(id=1, platform="youtube", video_id="resume-voice", url="https://example.test/video", title="续跑配音")
            voice_profile = VoiceProviderProfile(id=24, name="配音", provider_type="openai_tts", base_url="https://example.test/v1", api_key_encrypted="encrypted", voice="voice-model")
            job = AutomationJobRecord(
                id="auto-resume-voice",
                video_id=video.id,
                source_url=video.url,
                title=video.title,
                status="failed",
                params=json.dumps({
                    "url": video.url,
                    "enable_effects": False,
                    "enable_voice": True,
                    "voice_profile_id": 24,
                    "voice_mode": "batched",
                    "burn_subtitles": True,
                    "output_format": "mp4",
                    "workspace_dir": temp_dir,
                    "workspace_name": "resume-voice",
                    "video_downloads_dir": temp_dir,
                    "video_output_dir": temp_dir,
                    "video_exports_dir": temp_dir,
                    "source_video_path": downloaded_path,
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "parse", "status": "completed", "progress": 100, "task_id": None, "output_path": None, "error_message": None},
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": downloaded_path, "error_message": None},
                    {"key": "effects", "status": "skipped", "progress": 100, "task_id": 2, "output_path": downloaded_path, "error_message": None},
                    {"key": "subtitle", "status": "completed", "progress": 100, "task_id": 3, "output_path": subtitle_path, "error_message": None},
                    {"key": "voice", "status": "failed", "progress": 20, "task_id": 4, "output_path": None, "error_message": "旧失败"},
                    {"key": "export", "status": "pending", "progress": 0, "task_id": None, "output_path": None, "error_message": None},
                ], ensure_ascii=False),
            )
            db = FakeTaskDb([job], [], [video])
            task_ids = iter(range(240, 260))
            workspace_paths = {
                "workspace_dir": temp_dir,
                "workspace_name": "resume-voice",
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
                patch("backend.api.automation.LocalSpeechRecognizer", side_effect=AssertionError("续跑不应重新识别字幕")),
                patch("backend.api.automation.VoiceEngine", return_value=voice_engine),
                patch("backend.api.automation.decrypt_api_key", return_value="test-key"),
                patch("backend.api.automation._parse_or_update_video", return_value=video),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
                patch("backend.api.automation._pick_voice_profile", return_value=voice_profile),
                patch("backend.api.automation.ensure_video_workspace", return_value=workspace_paths),
            ):
                _run_automation_sync(
                    AutomationRunRequest(**json.loads(job.params)),
                    db,
                    job,
                    resume_from_checkpoint=True,
                )

        self.assertEqual([segment["text"] for segment in voice_engine.segments], ["续跑第一句", "续跑第二句"])
        self.assertEqual([segment["start_ms"] for segment in voice_engine.segments], [0, 1100])
        self.assertEqual(fake_processor.merge_calls[0]["audio_path"], os.path.join(temp_dir, "resume-voice_voice_smart.mp3"))

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

    def test_reexport_automation_job_applies_bitrate_during_subtitle_burn(self):
        """覆盖导出只有码率设置时，字幕烧录阶段直接控体积，避免烧完后再次慢速重编码"""
        with tempfile.TemporaryDirectory(prefix="automation_reexport_bitrate_") as temp_dir:
            source_video_path = os.path.join(temp_dir, "source.mp4")
            subtitle_path = os.path.join(temp_dir, "manual.srt")
            with open(source_video_path, "wb") as file:
                file.write(b"video")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("1\n00:00:00,000 --> 00:00:01,000\n中文译文\n")

            job = AutomationJobRecord(
                id="auto-reexport-bitrate",
                video_id=8,
                source_url="https://youtube.com/watch?v=bitrate",
                status="completed",
                params=json.dumps({
                    "output_format": "mp4",
                    "export_with_settings": True,
                    "export_settings": {
                        "resolution": "original",
                        "bitrate_enabled": True,
                        "bitrate_kbps": 1800,
                    },
                }, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": source_video_path, "error_message": None},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 2, "output_path": os.path.join(temp_dir, "old.mp4"), "error_message": None},
                ], ensure_ascii=False),
            )
            fake_processor = FakeAutomationProcessor(temp_dir)
            db = FakeTaskDb([job], [])
            task_ids = iter(range(45, 50))

            def fake_create_task(_db, video_id, task_type, params=None, parent_job_id=None):
                task = DownloadTask(video_id=video_id, task_type=task_type, params=json.dumps(params or {}, ensure_ascii=False), parent_job_id=parent_job_id)
                task.id = next(task_ids)
                return task

            with (
                patch("backend.api.automation.assert_required_tools_available"),
                patch("backend.api.automation.FFmpegProcessor", return_value=fake_processor),
                patch("backend.api.automation._create_task", side_effect=fake_create_task),
            ):
                reexport_automation_job("auto-reexport-bitrate", AutomationReExportRequest(subtitle_path=subtitle_path), db)

            burn_preset = fake_processor.burn_calls[0]["preset"]
            self.assertEqual(burn_preset["bitrate"]["fixed_kbps"]["value"], 1800)
            self.assertEqual(burn_preset["acceleration"]["quality"], "size")
            self.assertEqual(fake_processor.effects_calls, [])

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

    def test_reexport_automation_job_overwrites_original_output_after_success(self):
        """字幕调整覆盖导出会先写临时文件，成功后再替换原成品"""
        with tempfile.TemporaryDirectory(prefix="automation_reexport_overwrite_") as temp_dir:
            source_video_path = os.path.join(temp_dir, "source.mp4")
            output_path = os.path.join(temp_dir, "old.mp4")
            subtitle_path = os.path.join(temp_dir, "manual.ass")
            with open(source_video_path, "wb") as file:
                file.write(b"source")
            with open(output_path, "wb") as file:
                file.write(b"old")
            with open(subtitle_path, "w", encoding="utf-8") as file:
                file.write("""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,中文译文
""")

            job = AutomationJobRecord(
                id="auto-reexport-overwrite",
                video_id=9,
                source_url="https://youtube.com/watch?v=overwrite",
                status="completed",
                output_path=output_path,
                params=json.dumps({"output_format": "mp4", "export_with_settings": False}, ensure_ascii=False),
                stages=json.dumps([
                    {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": source_video_path, "error_message": None},
                    {"key": "export", "status": "completed", "progress": 100, "task_id": 2, "output_path": output_path, "error_message": None},
                ], ensure_ascii=False),
            )
            fake_processor = FakeAutomationProcessor(temp_dir)
            db = FakeTaskDb([job], [])
            task_ids = iter(range(50, 60))

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
                    "auto-reexport-overwrite",
                    AutomationReExportRequest(subtitle_path=subtitle_path, output_path=output_path),
                    db,
                )

            self.assertEqual(response.output_path, output_path)
            self.assertEqual(job.output_path, output_path)
            self.assertNotEqual(fake_processor.convert_calls[0]["output_path"], output_path)
            self.assertFalse(os.path.exists(fake_processor.convert_calls[0]["output_path"]))
            with open(output_path, "rb") as file:
                self.assertEqual(file.read(), b"exported")

    def test_prepare_export_rerun_keeps_previous_output_path(self):
        """重新导出开始时保留旧成品路径，避免中断后素材库找不到记录"""
        output_path = "D:/videos/final.mp4"
        job = AutomationJobRecord(
            id="auto-export-rerun-keep-output",
            source_url="https://youtube.com/watch?v=1",
            status="completed",
            output_path=output_path,
            stages=json.dumps([
                {"key": "download", "status": "completed", "progress": 100, "task_id": 1, "output_path": "D:/videos/source.mp4", "error_message": None},
                {"key": "export", "status": "completed", "progress": 100, "task_id": 2, "output_path": output_path, "error_message": None},
            ], ensure_ascii=False),
        )

        _prepare_job_export_stage_for_rerun(job)
        stages = {stage["key"]: stage for stage in json.loads(job.stages)}

        self.assertEqual(job.output_path, output_path)
        self.assertEqual(stages["export"]["output_path"], output_path)
        self.assertEqual(stages["export"]["status"], "pending")

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
            self.assertFalse([name for name in os.listdir(workspace_dir) if name.endswith(".txt")])
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

    def test_delete_job_folder_removes_record_when_files_already_missing(self):
        """素材文件夹已经被手动删除时，删除按钮只清理记录不报错"""
        missing_workspace = "D:/missing/videos/video-1"
        missing_output = "D:/missing/videos/video-1/exports/final.mp4"
        job = AutomationJobRecord(
            id="auto-missing-folder",
            source_url="https://youtube.com/watch?v=1",
            status="failed",
            output_path=missing_output,
            params=json.dumps({"workspace_dir": missing_workspace}, ensure_ascii=False),
            stages=json.dumps([
                {"key": "export", "status": "failed", "progress": 20, "task_id": 9, "output_path": missing_output, "error_message": "中断"},
            ], ensure_ascii=False),
        )
        task = DownloadTask(id=9, video_id=1, task_type="export", status="failed", parent_job_id=job.id, output_path=missing_output)
        db = FakeTaskDb([job], [task])

        response = delete_automation_job_folder(job.id, db)

        self.assertEqual(response.folder_path, "")
        self.assertEqual(db.jobs, [])
        self.assertEqual(db.tasks, [])

    def test_voice_for_segment_uses_speaker_map(self):
        """分段配音按说话人选择音色，未匹配时回退默认音色"""
        speaker_map = {"旁白": "alloy", "角色 A": "nova"}

        self.assertEqual(_voice_for_segment({"speaker": "角色 A"}, "onyx", speaker_map), "nova")
        self.assertEqual(_voice_for_segment({"speaker": "角色 B"}, "onyx", speaker_map), "onyx")

    def test_auto_voice_selector_keeps_explicit_default_voice_mapping(self):
        """自动多人音色不会把明确绑定默认音色的角色当成未知角色轮换"""
        speaker_map = {"旁白": "alloy", "角色 A": "nova", "角色 B": "onyx"}
        style_map = {"旁白": "解说风格", "角色 A": "年轻对话", "角色 B": "沉稳对话"}
        voice_selector = _build_auto_voice_selector("alloy", speaker_map)
        style_selector = _build_auto_style_selector(style_map, speaker_map, "alloy")

        self.assertEqual(voice_selector({"speaker": "旁白"}), "alloy")
        self.assertEqual(style_selector({"speaker": "旁白"}), "解说风格")
        self.assertEqual(voice_selector({"speaker": "路人甲"}), "nova")
        self.assertEqual(style_selector({"speaker": "路人甲"}), "年轻对话")

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
