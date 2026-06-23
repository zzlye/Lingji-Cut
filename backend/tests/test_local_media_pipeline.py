# backend/tests/test_local_media_pipeline.py
# 本地媒体管线测试 - 不依赖 YouTube 或外部 API，验证 ffmpeg 画面、字幕和导出链路

import os
import json
import shutil
import subprocess
import sys
import tempfile
import asyncio
import unittest
from unittest.mock import patch


# 嵌入式 Python 默认可能不读取 PYTHONPATH，测试文件直接运行时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.ffmpeg_processor import FFmpegProcessor
from backend.core.subtitle_engine import SubtitleEngine
from backend.core.voice_engine import VoiceEngine
from backend.api.effects import ProcessingConfig


class LocalMediaPipelineTest(unittest.TestCase):
    """本地媒体处理链路测试"""

    def setUp(self):
        """创建临时测试目录和 ffmpeg 处理器"""
        self.temp_dir = tempfile.mkdtemp(prefix="yt_pipeline_")
        self.processor = FFmpegProcessor()
        self.input_video = os.path.join(self.temp_dir, "input.mp4")
        self.ass_path = os.path.join(self.temp_dir, "subtitle.ass")
        self.voice_engine = VoiceEngine()

    def tearDown(self):
        """清理临时测试文件"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_effects_subtitles_and_export_generate_files(self):
        """画面处理、字幕烧录和导出都能生成可播放文件"""
        self._create_test_video()
        SubtitleEngine().generate_ass(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "测试字幕"}],
            self.ass_path,
            {"font_name": "Microsoft YaHei", "font_size": 24, "position": "bottom"},
        )

        enhanced_path = self.processor.apply_effects(
            video_path=self.input_video,
            preset=self._minimal_preset(),
            output_path=os.path.join(self.temp_dir, "enhanced.mp4"),
        )
        subtitled_path = self.processor.burn_subtitles(
            video_path=enhanced_path,
            subtitle_path=self.ass_path,
            output_path=os.path.join(self.temp_dir, "subtitled.mp4"),
        )
        exported_path = self.processor.convert_format(
            input_path=subtitled_path,
            output_format="mp4",
            output_path=os.path.join(self.temp_dir, "exported.mp4"),
        )

        for path in [enhanced_path, subtitled_path, exported_path]:
            self.assertTrue(os.path.exists(path), path)
            self.assertGreater(os.path.getsize(path), 0, path)

    def test_same_format_export_copies_without_ffmpeg(self):
        """同格式导出直接复制文件，避免跳过画面处理后还启动 ffmpeg"""
        self._create_test_video()
        output_path = os.path.join(self.temp_dir, "copied.mp4")
        progress_values: list[float] = []

        with patch.object(self.processor, "_run_ffmpeg") as run_ffmpeg:
            exported_path = self.processor.convert_format(
                input_path=self.input_video,
                output_format="mp4",
                output_path=output_path,
                progress_callback=progress_values.append,
            )

        self.assertEqual(exported_path, output_path)
        self.assertTrue(os.path.exists(output_path))
        self.assertEqual(os.path.getsize(output_path), os.path.getsize(self.input_video))
        self.assertEqual(progress_values[-1], 100)
        run_ffmpeg.assert_not_called()

    def test_timed_voice_mix_generates_aligned_audio_file(self):
        """带时间轴的分段音频可以混合成完整配音文件"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        second_audio = os.path.join(self.temp_dir, "second.wav")
        output_audio = os.path.join(self.temp_dir, "timed_voice.wav")
        self._create_test_audio(first_audio, 440)
        self._create_test_audio(second_audio, 880)

        result_path = self.voice_engine.mix_timed_audio_files([
            {"path": first_audio, "start_ms": 0, "duration_ms": 300},
            {"path": second_audio, "start_ms": 600, "duration_ms": 300},
        ], output_audio)

        self.assertTrue(os.path.exists(result_path))
        self.assertGreater(os.path.getsize(result_path), 0)

    def test_timed_voice_segment_temp_dir_follows_output_directory(self):
        """分段配音的临时片段目录应跟最终输出放在同一个视频目录里"""
        output_dir = os.path.join(self.temp_dir, "video_workspace", "output")
        output_audio = os.path.join(output_dir, "timed_voice.wav")
        temp_dir_calls: list[str] = []

        async def fake_generate_voice(*_, output_path: str, **__):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as file:
                file.write(b"segment")
            return output_path

        def fake_mix(_timed_audio_paths, final_output_path: str, **_kwargs):
            os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
            with open(final_output_path, "wb") as file:
                file.write(b"voice")
            return final_output_path

        def fake_mkdtemp(prefix: str, dir: str):
            temp_dir_calls.append(dir)
            path = os.path.join(dir, f"{prefix}tmp")
            os.makedirs(path, exist_ok=True)
            return path

        with (
            patch.object(self.voice_engine, "generate_voice", side_effect=fake_generate_voice),
            patch.object(self.voice_engine, "stitch_timed_audio_files", side_effect=fake_mix),
            patch("backend.core.voice_engine.tempfile.mkdtemp", side_effect=fake_mkdtemp),
        ):
            result_path = asyncio.run(self.voice_engine.generate_timed_voice_track(
                segments=[{"start_ms": 0, "end_ms": 600, "text": "测试配音"}],
                output_path=output_audio,
            ))

        self.assertEqual(result_path, output_audio)
        self.assertEqual(temp_dir_calls, [output_dir])
        self.assertTrue(os.path.exists(output_audio))

    def test_batched_timed_voice_generates_each_subtitle_separately(self):
        """批量时间轴配音逐条生成音频，最后再按字幕时间混合"""
        output_audio = os.path.join(self.temp_dir, "batched_voice.wav")
        generated: list[dict[str, str]] = []
        stitched_items: list[dict] = []

        async def fake_generate_voice(text: str, output_path: str, voice: str = "", settings=None, **_kwargs):
            generated.append({"text": text, "voice": voice, "style": str((settings or {}).get("style_prompt") or "")})
            with open(output_path, "wb") as file:
                file.write(b"segment")
            return output_path

        def fake_stitch(timed_audio_paths, final_output_path: str, **_kwargs):
            stitched_items.extend(timed_audio_paths)
            with open(final_output_path, "wb") as file:
                file.write(b"voice")
            return final_output_path

        with (
            patch.object(self.voice_engine, "generate_voice", side_effect=fake_generate_voice),
            patch.object(self.voice_engine, "stitch_timed_audio_files", side_effect=fake_stitch),
        ):
            result_path = asyncio.run(self.voice_engine.generate_batched_timed_voice_track(
                segments=[
                    {"start_ms": 0, "end_ms": 3000, "text": "第一句", "speaker": "旁白"},
                    {"start_ms": 3500, "end_ms": 6000, "text": "第二句", "speaker": "角色A"},
                ],
                output_path=output_audio,
                voice="alloy",
                voice_selector=lambda segment: "nova" if segment.get("speaker") == "角色A" else "alloy",
                style_selector=lambda segment: "对话风格" if segment.get("speaker") == "角色A" else "解说风格",
                settings={"voice_batch_size": 8, "voice_batch_chars": 900, "voice_concurrency": 1},
            ))

        self.assertEqual(result_path, output_audio)
        self.assertEqual([item["text"] for item in generated], ["第一句", "第二句"])
        self.assertEqual([item["voice"] for item in generated], ["alloy", "nova"])
        self.assertTrue(generated[0]["style"].startswith("解说风格"))
        self.assertTrue(generated[1]["style"].startswith("对话风格"))
        self.assertEqual([item["start_ms"] for item in stitched_items], [0, 3500])
        self.assertEqual([item["original_start_ms"] for item in stitched_items], [0, 3500])
        self.assertEqual([item["duration_ms"] for item in stitched_items], [3000, 2500])

    def test_batched_timed_voice_writes_timeline_metadata(self):
        """批量配音完成后保存真实片段时长，供最终字幕按配音尾音同步"""
        output_audio = os.path.join(self.temp_dir, "batched_voice.wav")
        generated_settings: list[dict] = []

        async def fake_generate_voice(text: str, output_path: str, settings=None, **_kwargs):
            generated_settings.append(settings or {})
            with open(output_path, "wb") as file:
                file.write(f"voice:{text}".encode("utf-8"))
            return output_path

        def fake_mix(_timed_audio_paths, final_output_path: str, **_kwargs):
            with open(final_output_path, "wb") as file:
                file.write(b"voice")
            return final_output_path

        with (
            patch.object(self.voice_engine, "generate_voice", side_effect=fake_generate_voice),
            patch.object(self.voice_engine, "stitch_timed_audio_files", side_effect=fake_mix),
            patch.object(self.voice_engine, "_audio_duration_seconds", return_value=1.42),
        ):
            result_path = asyncio.run(self.voice_engine.generate_batched_timed_voice_track(
                segments=[{"start_ms": 1000, "end_ms": 4200, "text": "测试配音"}],
                output_path=output_audio,
                settings={"style_prompt": "自然男声"},
            ))

        metadata_path = VoiceEngine.timeline_metadata_path(result_path)
        self.assertTrue(os.path.isfile(metadata_path))
        with open(metadata_path, "r", encoding="utf-8") as file:
            metadata = json.load(file)

        self.assertEqual(metadata["segments"][0]["start_ms"], 1000)
        self.assertEqual(metadata["segments"][0]["duration_ms"], 3200)
        self.assertEqual(metadata["segments"][0]["source_duration_ms"], 1420)
        self.assertIn("3.2 秒", generated_settings[0]["style_prompt"])

    def test_batched_timed_voice_plans_timeline_without_overlap(self):
        """配音真实时长超过原字幕窗时，应顺延后续片段而不是叠加抢话"""
        output_audio = os.path.join(self.temp_dir, "batched_voice.wav")
        stitched_items: list[dict] = []
        durations = [1.8, 0.4]

        async def fake_generate_voice(text: str, output_path: str, **_kwargs):
            with open(output_path, "wb") as file:
                file.write(f"voice:{text}".encode("utf-8"))
            return output_path

        def fake_duration(_path: str):
            return durations.pop(0) if durations else 0.4

        def fake_stitch(timed_audio_paths, final_output_path: str, **_kwargs):
            stitched_items.extend(timed_audio_paths)
            with open(final_output_path, "wb") as file:
                file.write(b"voice")
            return final_output_path

        with (
            patch.object(self.voice_engine, "generate_voice", side_effect=fake_generate_voice),
            patch.object(self.voice_engine, "stitch_timed_audio_files", side_effect=fake_stitch),
            patch.object(self.voice_engine, "_audio_duration_seconds", side_effect=fake_duration),
        ):
            result_path = asyncio.run(self.voice_engine.generate_batched_timed_voice_track(
                segments=[
                    {"start_ms": 0, "end_ms": 500, "text": "第一句"},
                    {"start_ms": 600, "end_ms": 1000, "text": "第二句"},
                ],
                output_path=output_audio,
                settings={"voice_min_gap_ms": 300, "voice_max_speed": 1.0},
            ))

        self.assertEqual(result_path, output_audio)
        self.assertEqual(stitched_items[0]["start_ms"], 0)
        self.assertEqual(stitched_items[0]["source_duration_ms"], 1800)
        self.assertEqual(stitched_items[1]["start_ms"], 2100)
        self.assertEqual(stitched_items[1]["original_start_ms"], 600)
        with open(VoiceEngine.timeline_metadata_path(output_audio), "r", encoding="utf-8") as file:
            metadata = json.load(file)
        self.assertEqual([item["start_ms"] for item in metadata["segments"]], [0, 2100])
        self.assertEqual(metadata["segments"][0]["audio_end_ms"], 1800)

    def test_batched_voice_chunks_segments_without_merging_text(self):
        """批量配音只分批调度，不合并字幕文本，避免批次内部时间轴漂移"""
        segments = [
            {"start_ms": 0, "end_ms": 500, "text": "第一句", "speaker": "旁白"},
            {"start_ms": 500, "end_ms": 1000, "text": "第二句", "speaker": "旁白"},
            {"start_ms": 1000, "end_ms": 1500, "text": "第三句", "speaker": "角色A"},
            {"start_ms": 1500, "end_ms": 2000, "text": "第四句", "speaker": "角色A"},
            {"start_ms": 2000, "end_ms": 2500, "text": "第五句", "speaker": "角色A"},
        ]

        chunks = self.voice_engine._chunk_timed_segments(segments, batch_size=2, max_chars=100)

        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 1])
        self.assertEqual([[segment["text"] for segment in chunk] for chunk in chunks], [["第一句", "第二句"], ["第三句", "第四句"], ["第五句"]])

    def test_grouped_timed_voice_uses_videolingo_timeline_compatibility(self):
        """旧分组入口也统一走 VideoLingo 式逐条配音时间轴"""
        output_audio = os.path.join(self.temp_dir, "grouped_voice.wav")
        generated: list[dict[str, str]] = []
        stitched_items: list[dict] = []

        async def fake_generate_voice(text: str, output_path: str, voice: str = "", settings=None, **_kwargs):
            generated.append({
                "text": text,
                "voice": voice,
                "style": str((settings or {}).get("style_prompt") or ""),
            })
            with open(output_path, "wb") as file:
                file.write(b"group")
            return output_path

        def fake_duration(path: str):
            _ = path
            return 0.5

        def fake_stitch(timed_audio_paths, final_output_path: str, **_kwargs):
            stitched_items.extend(timed_audio_paths)
            with open(final_output_path, "wb") as file:
                file.write(b"voice")
            return final_output_path

        with (
            patch.object(self.voice_engine, "generate_voice", side_effect=fake_generate_voice),
            patch.object(self.voice_engine, "_audio_duration_seconds", side_effect=fake_duration),
            patch.object(self.voice_engine, "stitch_timed_audio_files", side_effect=fake_stitch),
            patch.object(self.voice_engine, "mix_timed_audio_files") as legacy_mix,
        ):
            result_path = asyncio.run(self.voice_engine.generate_grouped_timed_voice_track(
                segments=[
                    {"start_ms": 0, "end_ms": 3000, "text": "第一句", "speaker": "旁白"},
                    {"start_ms": 3000, "end_ms": 6000, "text": "第二句", "speaker": "旁白"},
                    {"start_ms": 6500, "end_ms": 9500, "text": "第三句", "speaker": "角色A"},
                ],
                output_path=output_audio,
                voice="alloy",
                voice_selector=lambda segment: "nova" if segment.get("speaker") == "角色A" else "alloy",
                settings={"voice_group_size": 6, "voice_group_chars": 500, "voice_group_max_seconds": 12, "voice_group_gap_ms": 800, "voice_concurrency": 1},
            ))

        self.assertEqual(result_path, output_audio)
        self.assertEqual([item["text"] for item in generated], ["第一句", "第二句", "第三句"])
        self.assertEqual([item["voice"] for item in generated], ["alloy", "alloy", "nova"])
        self.assertTrue(all("秒内自然说完" in item["style"] for item in generated))
        self.assertEqual([item["start_ms"] for item in stitched_items], [0, 3000, 6500])
        self.assertEqual([item["duration_ms"] for item in stitched_items], [3000, 3000, 3000])
        legacy_mix.assert_not_called()

    def test_timed_voice_mix_keeps_natural_segment_duration_by_default(self):
        """默认只按字幕开始时间放置配音，不再强制变速或裁剪尾音"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        output_audio = os.path.join(self.temp_dir, "timed_voice.wav")
        self._create_test_audio(first_audio, 440)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(output_audio, "wb") as file:
                file.write(b"voice")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("backend.core.voice_engine.subprocess.run", side_effect=fake_run):
            result_path = self.voice_engine.mix_timed_audio_files([
                {"path": first_audio, "start_ms": 1200, "duration_ms": 800, "source_duration_ms": 400},
            ], output_audio)

        self.assertEqual(result_path, output_audio)
        filter_arg = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertNotIn("atempo=", filter_arg)
        self.assertNotIn("atrim=", filter_arg)
        self.assertIn("adelay=1200:all=1", filter_arg)

    def test_timed_voice_mix_delays_next_segment_to_avoid_overlap(self):
        """逐条配音真实尾音超过字幕窗时，下一句会顺延留出安全间隔"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        second_audio = os.path.join(self.temp_dir, "second.wav")
        output_audio = os.path.join(self.temp_dir, "timed_voice.wav")
        self._create_test_audio(first_audio, 440)
        self._create_test_audio(second_audio, 880)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(output_audio, "wb") as file:
                file.write(b"voice")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("backend.core.voice_engine.subprocess.run", side_effect=fake_run):
            result_path = self.voice_engine.mix_timed_audio_files([
                {"path": first_audio, "start_ms": 0, "duration_ms": 500, "source_duration_ms": 900},
                {"path": second_audio, "start_ms": 600, "duration_ms": 500, "source_duration_ms": 300},
            ], output_audio)

        self.assertEqual(result_path, output_audio)
        filter_arg = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("adelay=0:all=1", filter_arg)
        self.assertIn("adelay=1200:all=1", filter_arg)

    def test_timed_voice_mix_zero_gap_still_prevents_overlap(self):
        """0ms 只表示不额外留空，不能允许两段配音同时响"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        second_audio = os.path.join(self.temp_dir, "second.wav")
        output_audio = os.path.join(self.temp_dir, "timed_voice.wav")
        self._create_test_audio(first_audio, 440)
        self._create_test_audio(second_audio, 880)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(output_audio, "wb") as file:
                file.write(b"voice")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("backend.core.voice_engine.subprocess.run", side_effect=fake_run):
            result_path = self.voice_engine.mix_timed_audio_files([
                {"path": first_audio, "start_ms": 0, "duration_ms": 500, "source_duration_ms": 900},
                {"path": second_audio, "start_ms": 600, "duration_ms": 500, "source_duration_ms": 300},
            ], output_audio, min_gap_ms=0)

        self.assertEqual(result_path, output_audio)
        filter_arg = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("adelay=0:all=1", filter_arg)
        self.assertIn("adelay=900:all=1", filter_arg)

    def test_timed_voice_stitch_uses_concat_instead_of_amix(self):
        """智能配音最终轨按静音和音频串接，不再把多句配音混叠到一起"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        second_audio = os.path.join(self.temp_dir, "second.wav")
        output_audio = os.path.join(self.temp_dir, "stitched_voice.wav")
        self._create_test_audio(first_audio, 440)
        self._create_test_audio(second_audio, 880)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(output_audio, "wb") as file:
                file.write(b"voice")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("backend.core.voice_engine.subprocess.run", side_effect=fake_run):
            result_path = self.voice_engine.stitch_timed_audio_files([
                {"path": first_audio, "start_ms": 0, "duration_ms": 500, "source_duration_ms": 900},
                {"path": second_audio, "start_ms": 1200, "duration_ms": 500, "source_duration_ms": 300},
            ], output_audio)

        self.assertEqual(result_path, output_audio)
        joined_calls = " ".join(" ".join(cmd) for cmd in calls)
        final_cmd = calls[-1]
        self.assertIn("-f", final_cmd)
        self.assertIn("concat", final_cmd)
        self.assertNotIn("amix=", joined_calls)
        self.assertNotIn("adelay=", joined_calls)

    def test_grouped_voice_timeline_metadata_expands_nested_segments(self):
        """分组配音元数据按组内字幕展开，避免第一条字幕吃掉整组配音时长"""
        output_audio = os.path.join(self.temp_dir, "grouped_voice.wav")
        self.voice_engine._write_timeline_metadata(output_audio, [{
            "path": output_audio,
            "start_ms": 1000,
            "original_start_ms": 800,
            "duration_ms": 2000,
            "source_duration_ms": 2400,
            "text": "第一句，第二句",
            "segments": [
                {"start_ms": 800, "end_ms": 1800, "text": "第一句", "speaker": "旁白"},
                {"start_ms": 1800, "end_ms": 2800, "text": "第二句", "speaker": "旁白"},
            ],
        }])

        with open(VoiceEngine.timeline_metadata_path(output_audio), "r", encoding="utf-8") as file:
            metadata = json.load(file)

        self.assertEqual([item["text"] for item in metadata["segments"]], ["第一句", "第二句"])
        self.assertEqual([item["original_start_ms"] for item in metadata["segments"]], [800, 1800])
        self.assertEqual(metadata["segments"][0]["audio_end_ms"], metadata["segments"][1]["start_ms"])
        self.assertEqual(metadata["segments"][1]["audio_end_ms"], 3400)

    def test_timed_voice_mix_can_fit_segment_to_subtitle_window_when_enabled(self):
        """显式开启贴合字幕窗口时才允许变速和裁剪，作为兼容开关保留"""
        first_audio = os.path.join(self.temp_dir, "first.wav")
        output_audio = os.path.join(self.temp_dir, "timed_voice.wav")
        self._create_test_audio(first_audio, 440)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            with open(output_audio, "wb") as file:
                file.write(b"voice")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("backend.core.voice_engine.subprocess.run", side_effect=fake_run):
            result_path = self.voice_engine.mix_timed_audio_files([
                {"path": first_audio, "start_ms": 1200, "duration_ms": 800, "source_duration_ms": 400},
            ], output_audio, fit_to_subtitle_window=True)

        self.assertEqual(result_path, output_audio)
        filter_arg = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("atempo=0.500", filter_arg)
        self.assertNotIn("atrim=", filter_arg)

    def test_background_audio_mode_uses_local_ai_no_vocals_before_mix(self):
        """保留背景声模式必须使用本地 AI 分离出的 no_vocals 轨再叠加配音"""
        self._create_test_video()
        voice_audio = os.path.join(self.temp_dir, "voice.wav")
        background_audio = os.path.join(self.temp_dir, "ai_no_vocals.wav")
        output_video = os.path.join(self.temp_dir, "background_mix.mp4")
        self._create_test_audio(voice_audio, 660)
        self._create_test_audio(background_audio, 220)
        calls: list[list[str]] = []

        def fake_run(cmd, *_args, **_kwargs):
            calls.append(cmd)
            with open(output_video, "wb") as file:
                file.write(b"video")
            return output_video

        with (
            patch.object(self.processor, "_separate_background_audio_with_local_ai", return_value=background_audio) as separate_background,
            patch.object(self.processor, "_run_ffmpeg", side_effect=fake_run),
        ):
            result_path = self.processor.merge_audio_video(
                video_path=self.input_video,
                audio_path=voice_audio,
                output_path=output_video,
                mode="background",
                volume_ratio=0.35,
            )

        self.assertEqual(result_path, output_video)
        separate_background.assert_called_once()
        self.assertIn(background_audio, calls[0])
        filter_arg = calls[0][calls[0].index("-filter_complex") + 1]
        self.assertIn("volume=0.35", filter_arg)
        self.assertIn("loudnorm=I=-16", filter_arg)
        self.assertIn("sidechaincompress", filter_arg)
        self.assertIn("[bg_ducked][voice]amix", filter_arg)

    def test_background_audio_mode_requires_local_ai_separator(self):
        """保留背景声模式找不到本地 AI 分离模型时直接报错，不再退回声道抵消"""
        self._create_test_video()
        voice_audio = os.path.join(self.temp_dir, "voice.wav")
        output_video = os.path.join(self.temp_dir, "background_mix.mp4")
        self._create_test_audio(voice_audio, 660)

        with patch.object(self.processor, "_demucs_command_prefix", return_value=[]):
            with self.assertRaises(RuntimeError) as context:
                self.processor.merge_audio_video(
                    video_path=self.input_video,
                    audio_path=voice_audio,
                    output_path=output_video,
                    mode="background",
                    volume_ratio=0.35,
                )

        self.assertIn("本地 AI 去人声不可用", str(context.exception))

    def test_demucs_command_prefers_project_runner_when_module_available(self):
        """本地 AI 去人声优先使用项目内 Demucs 启动器，避开 torchaudio 保存兼容问题"""
        fake_python = os.path.join(self.temp_dir, "python.exe")
        with open(fake_python, "wb") as file:
            file.write(b"python")

        with (
            patch.object(self.processor, "_python_has_module", return_value=True),
            patch("backend.core.ffmpeg_processor.sys.executable", fake_python),
        ):
            command_prefix = self.processor._demucs_command_prefix()

        self.assertEqual(command_prefix[0], fake_python)
        self.assertTrue(command_prefix[1].endswith(os.path.join("backend", "core", "demucs_runner.py")))

    def test_auto_acceleration_prefers_available_gpu_encoder(self):
        """自动硬件加速会优先选择可用 GPU 编码器"""
        with patch("backend.core.ffmpeg_processor.working_gpu_encoders", return_value=("h264_nvenc",)):
            encoder = self.processor._resolve_video_encoder({
                "acceleration": {"enabled": True, "mode": "auto", "quality": "balanced"},
            })

        self.assertEqual(encoder, "h264_nvenc")

    def test_unavailable_gpu_encoder_falls_back_to_cpu(self):
        """指定的 GPU 编码器不可用时回退 CPU，避免画面处理直接失败"""
        with patch("backend.core.ffmpeg_processor.working_gpu_encoders", return_value=()):
            encoder = self.processor._resolve_video_encoder({
                "acceleration": {"enabled": True, "mode": "nvidia", "quality": "balanced"},
            })

        self.assertEqual(encoder, "libx264")

    def test_unavailable_selected_gpu_falls_back_to_working_gpu(self):
        """指定 GPU 不可用时优先切换到实测可用 GPU，而不是直接走慢速 CPU"""
        with patch("backend.core.ffmpeg_processor.working_gpu_encoders", return_value=("h264_nvenc",)):
            encoder = self.processor._resolve_video_encoder({
                "acceleration": {"enabled": True, "mode": "intel", "quality": "size"},
            })

        self.assertEqual(encoder, "h264_nvenc")

    def test_gpu_runtime_failure_retries_with_cpu_encoder(self):
        """GPU 编码器运行失败时自动重试 CPU 编码"""
        output_path = os.path.join(self.temp_dir, "fallback.mp4")
        gpu_cmd = [self.processor.ffmpeg_cmd, "-i", self.input_video, "-c:v", "h264_nvenc", "-preset", "p4", "-y", output_path]
        calls: list[list[str]] = []

        def fake_run_ffmpeg(cmd, action_name, timeout=600, control_keys=None, progress_callback=None, progress_total_seconds=None):
            calls.append(cmd)
            if "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "h264_nvenc":
                raise RuntimeError(f"{action_name}失败: Cannot load nvcuda.dll")
            return output_path

        with patch.object(self.processor, "_run_ffmpeg", side_effect=fake_run_ffmpeg):
            result = self.processor._run_ffmpeg_with_cpu_fallback(
                gpu_cmd,
                "画面处理",
                timeout=30,
                encoder="h264_nvenc",
                preset={"acceleration": {"enabled": True, "mode": "nvidia", "quality": "balanced"}},
                for_subtitles=False,
            )

        self.assertEqual(result, output_path)
        self.assertEqual(calls[0][calls[0].index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(calls[1][calls[1].index("-c:v") + 1], "libx264")

    def test_gpu_effects_command_does_not_mix_cpu_crf(self):
        """GPU 画面处理使用硬件质量参数，不混入 CPU 的 crf 参数"""
        self._create_test_video()
        output_path = os.path.join(self.temp_dir, "gpu_args.mp4")
        calls: list[list[str]] = []

        def fake_run_ffmpeg(cmd, action_name, timeout=600, control_keys=None, progress_callback=None, progress_total_seconds=None):
            calls.append(cmd)
            return output_path

        with (
            patch("backend.core.ffmpeg_processor.working_gpu_encoders", return_value=("h264_nvenc",)),
            patch.object(self.processor, "_run_ffmpeg", side_effect=fake_run_ffmpeg),
        ):
            result = self.processor.apply_effects(
                video_path=self.input_video,
                preset={
                    "adjustments": {"enabled": False},
                    "canvas": {"enabled": False},
                    "transform": {"enabled": False},
                    "timing": {"enabled": False},
                    "bitrate": {"enabled": False},
                    "acceleration": {"enabled": True, "mode": "auto", "quality": "size"},
                },
                output_path=output_path,
            )

        self.assertEqual(result, output_path)
        self.assertEqual(calls[0][calls[0].index("-c:v") + 1], "h264_nvenc")
        self.assertIn("-cq", calls[0])
        self.assertNotIn("-crf", calls[0])

    def test_burn_subtitles_prefers_gpu_encoder_when_available(self):
        """字幕烧录优先使用 GPU 编码，保证有显卡的电脑速度更快"""
        self._create_test_video()
        SubtitleEngine().generate_ass(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "测试字幕"}],
            self.ass_path,
            {"font_name": "Microsoft YaHei", "font_size": 24, "position": "bottom"},
        )
        output_path = os.path.join(self.temp_dir, "subtitled_cpu.mp4")

        with (
            patch("backend.core.ffmpeg_processor.working_gpu_encoders", return_value=("h264_nvenc",)),
            patch.object(self.processor, "_media_duration_seconds", return_value=1.0),
            patch.object(self.processor, "_run_ffmpeg_with_cpu_fallback", return_value=output_path) as run_ffmpeg,
        ):
            result = self.processor.burn_subtitles(
                video_path=self.input_video,
                subtitle_path=self.ass_path,
                output_path=output_path,
                preset={"acceleration": {"enabled": True, "mode": "auto", "quality": "quality"}},
            )

        cmd = run_ffmpeg.call_args.args[0]
        self.assertEqual(result, output_path)
        self.assertEqual(run_ffmpeg.call_args.kwargs["encoder"], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertIn("-cq", cmd)

    def test_ass_subtitle_filter_preserves_embedded_styles(self):
        """ASS 字幕烧录不能再注入 force_style，否则第二行颜色和字号会被覆盖"""
        subtitle_filter = self.processor._build_subtitle_filter(
            os.path.join(self.temp_dir, "double_line.ass"),
            {
                "font_name": "Microsoft YaHei",
                "font_size": 44,
                "secondary_font_size": 42,
                "font_color": "#FFFFFF",
                "secondary_color": "#FDE68A",
            },
        )

        self.assertEqual(subtitle_filter, f"subtitles='{self.temp_dir.replace('\\', '/').replace(':', '\\:')}/double_line.ass'")
        self.assertNotIn("force_style", subtitle_filter)

    def test_burn_subtitles_handles_single_quote_in_ass_path(self):
        """ASS 文件名带单引号时也能烧录，避免 Minecraft's 这类标题导致滤镜路径截断"""
        self._create_test_video()
        quoted_ass_path = os.path.join(self.temp_dir, "Minecraft's Secret.ass")
        SubtitleEngine().generate_ass(
            [{"index": 1, "start": "00:00:00,000", "end": "00:00:01,000", "text": "测试字幕"}],
            quoted_ass_path,
            {"font_name": "Microsoft YaHei", "font_size": 24, "position": "bottom"},
        )
        output_path = os.path.join(self.temp_dir, "quoted_subtitled.mp4")

        result = self.processor.burn_subtitles(
            video_path=self.input_video,
            subtitle_path=quoted_ass_path,
            output_path=output_path,
            preset={"acceleration": {"enabled": False}},
        )

        self.assertEqual(result, output_path)
        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 0)

    def test_srt_subtitle_filter_still_uses_force_style(self):
        """普通字幕文件仍通过 force_style 注入基础样式"""
        subtitle_filter = self.processor._build_subtitle_filter(
            os.path.join(self.temp_dir, "plain.srt"),
            {
                "font_name": "Microsoft YaHei",
                "font_size": 44,
                "font_color": "#FFFFFF",
                "secondary_color": "#FDE68A",
            },
        )

        self.assertIn("force_style=", subtitle_filter)
        self.assertIn("PrimaryColour=&H00FFFFFF", subtitle_filter)

    def test_parse_video_size_from_ffmpeg_output(self):
        """没有 ffprobe 时也能从 ffmpeg 输出解析视频分辨率"""
        output = "Stream #0:0: Video: h264, yuv420p(tv, bt709), 1920x1080, 60 fps"

        self.assertEqual(self.processor._parse_video_size(output), (1920, 1080))

    def test_subtitle_cpu_encoder_uses_smaller_default_crf(self):
        """字幕烧录默认 CPU 编码略偏体积优先，避免没做画面处理时成品暴涨"""
        args = self.processor._video_encoder_args({}, for_subtitles=True, encoder="libx264")

        self.assertEqual(args[args.index("-preset") + 1], "fast")
        self.assertEqual(args[args.index("-crf") + 1], "23")
        self.assertIn("yuv420p", args)

    def test_subtitle_nvenc_encoder_uses_safe_quality_args(self):
        """字幕烧录的 NVENC 参数避免低延迟模式，减少花屏风险"""
        args = self.processor._video_encoder_args(
            {"acceleration": {"enabled": True, "mode": "nvidia", "quality": "balanced"}},
            for_subtitles=True,
            encoder="h264_nvenc",
        )

        self.assertEqual(args[args.index("-preset") + 1], "p4")
        self.assertEqual(args[args.index("-cq") + 1], "23")
        self.assertNotIn("ull", args)
        self.assertNotIn("-zerolatency", args)
        self.assertNotIn("-rc-lookahead", args)

    def test_subtitle_nvenc_encoder_uses_requested_bitrate_when_present(self):
        """字幕烧录如果带了输出码率策略，就不要再强制 b:v 0 放任码率飙升"""
        args = self.processor._video_encoder_args(
            {
                "bitrate": {
                    "enabled": True,
                    "mode": "fixed",
                    "fixed_kbps": {"enabled": True, "random": False, "value": 2200, "min": 2200, "max": 2200},
                },
                "acceleration": {"enabled": True, "mode": "nvidia", "quality": "size"},
            },
            for_subtitles=True,
            encoder="h264_nvenc",
        )

        self.assertEqual(args[args.index("-cq") + 1], "26")
        self.assertNotIn("-b:v", args)

    def test_default_processing_config_is_fast_1080p(self):
        """默认画面处理保持 1080p 输出，同时关闭重 CPU 滤镜"""
        preset = ProcessingConfig().model_dump()
        filter_graph = self.processor.build_effect_filter_graph(preset)

        self.assertIn("scale=1920:1080", filter_graph)
        self.assertIn("flags=fast_bilinear", filter_graph)
        self.assertNotIn("eq=", filter_graph)
        self.assertNotIn("unsharp", filter_graph)
        self.assertNotIn("hqdn3d", filter_graph)
        self.assertNotIn("rotate=", filter_graph)
        self.assertNotIn("fps=", filter_graph)
        self.assertEqual(preset["bitrate"]["fixed_kbps"]["value"], 2200)
        self.assertEqual(preset["bitrate"]["quality_mode"], "size")
        self.assertEqual(preset["acceleration"]["quality"], "size")

    def test_drop_frame_does_not_rewrite_video_timestamps(self):
        """抽帧不重写 PTS，避免画面时长变化后音频 copy 导致音画不同步"""
        preset = self._minimal_preset()
        preset["timing"] = {
            "enabled": True,
            "fps": {"enabled": False, "random": False, "value": 30, "min": 30, "max": 30},
            "drop_frame": {"enabled": True, "interval": {"enabled": True, "random": False, "value": 25, "min": 25, "max": 25}},
            "dynamic_zoom": {"enabled": False, "random": False, "value": 0, "min": 0, "max": 0},
        }

        filter_graph = self.processor.build_effect_filter_graph(preset)

        self.assertIn("select=", filter_graph)
        self.assertNotIn("setpts=N/FRAME_RATE/TB", filter_graph)

    def test_nvenc_speed_quality_uses_low_latency_single_pass_args(self):
        """速度优先的 NVENC 参数减少额外编码开销"""
        args = self.processor._video_encoder_args(
            {"acceleration": {"enabled": True, "mode": "nvidia", "quality": "size"}},
            for_subtitles=False,
            encoder="h264_nvenc",
        )

        self.assertIn("p1", args)
        self.assertIn("ull", args)
        self.assertIn("disabled", args)
        self.assertIn("0", args)

    def test_effects_reports_progress_during_ffmpeg_run(self):
        """画面处理会从 ffmpeg 进度输出同步百分比"""
        self._create_test_video()
        progress_values: list[float] = []

        output_path = self.processor.apply_effects(
            video_path=self.input_video,
            preset=self._minimal_preset(),
            output_path=os.path.join(self.temp_dir, "progress.mp4"),
            progress_callback=progress_values.append,
        )

        self.assertTrue(os.path.exists(output_path))
        self.assertTrue(progress_values)
        self.assertEqual(progress_values[-1], 100)
        self.assertTrue(any(value > 0 for value in progress_values))

    def test_mix_audio_uses_video_audio_duration(self):
        """混合配音时按原视频音频时长输出，避免 voice 较短导致音画截断"""
        self._create_test_video()
        voice_path = os.path.join(self.temp_dir, "voice.wav")
        output_path = os.path.join(self.temp_dir, "mixed.mp4")
        self._create_test_audio(voice_path, 440)
        calls: list[list[str]] = []

        def fake_run_ffmpeg(cmd, action_name, timeout=600, control_keys=None, progress_callback=None, progress_total_seconds=None):
            calls.append(cmd)
            return output_path

        with patch.object(self.processor, "_run_ffmpeg", side_effect=fake_run_ffmpeg):
            result = self.processor.merge_audio_video(
                video_path=self.input_video,
                audio_path=voice_path,
                output_path=output_path,
                mode="mix",
                volume_ratio=0.25,
            )

        self.assertEqual(result, output_path)
        self.assertTrue(any("duration=first" in part for part in calls[0]))

    def test_replace_audio_does_not_use_shortest(self):
        """替换配音不使用 shortest，避免配音短于视频时直接截短画面"""
        self._create_test_video()
        voice_path = os.path.join(self.temp_dir, "voice.wav")
        output_path = os.path.join(self.temp_dir, "replaced.mp4")
        self._create_test_audio(voice_path, 440)
        calls: list[list[str]] = []

        def fake_run_ffmpeg(cmd, action_name, timeout=600, control_keys=None, progress_callback=None, progress_total_seconds=None):
            calls.append(cmd)
            return output_path

        with (
            patch.object(self.processor, "_media_duration_seconds", return_value=1.0),
            patch.object(self.processor, "_run_ffmpeg", side_effect=fake_run_ffmpeg),
        ):
            result = self.processor.merge_audio_video(
                video_path=self.input_video,
                audio_path=voice_path,
                output_path=output_path,
                mode="replace",
            )

        self.assertEqual(result, output_path)
        self.assertNotIn("-shortest", calls[0])
        self.assertIn("-t", calls[0])

    def _create_test_video(self):
        """用 ffmpeg 生成短测试视频"""
        cmd = [
            self.processor.ffmpeg_cmd,
            "-f", "lavfi",
            "-i", "testsrc=size=320x180:rate=24:duration=1",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-shortest",
            "-y",
            self.input_video,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if result.returncode != 0:
            self.fail(f"生成测试视频失败: {result.stderr}")

    def _create_test_audio(self, output_path: str, frequency: int):
        """用 ffmpeg 生成短测试音频"""
        cmd = [
            self.processor.ffmpeg_cmd,
            "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration=0.4",
            "-c:a", "pcm_s16le",
            "-y",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if result.returncode != 0:
            self.fail(f"生成测试音频失败: {result.stderr}")

    def _minimal_preset(self):
        """最小处理参数，保证测试快速稳定"""
        return {
            "adjustments": {"enabled": False},
            "canvas": {"enabled": False},
            "transform": {"enabled": False},
            "timing": {"enabled": False},
            "bitrate": {"enabled": False},
            "acceleration": {"enabled": False, "mode": "cpu", "quality": "balanced"},
        }


if __name__ == "__main__":
    unittest.main()
