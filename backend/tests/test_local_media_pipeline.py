# backend/tests/test_local_media_pipeline.py
# 本地媒体管线测试 - 不依赖 YouTube 或外部 API，验证 ffmpeg 画面、字幕和导出链路

import os
import shutil
import subprocess
import sys
import tempfile
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

    def test_subtitle_cpu_encoder_uses_high_quality_crf(self):
        """字幕烧录的 CPU 编码使用高质量 CRF，减少重编码糊块"""
        args = self.processor._video_encoder_args({}, for_subtitles=True, encoder="libx264")

        self.assertEqual(args[args.index("-preset") + 1], "fast")
        self.assertEqual(args[args.index("-crf") + 1], "20")
        self.assertIn("yuv420p", args)

    def test_subtitle_nvenc_encoder_uses_safe_quality_args(self):
        """字幕烧录的 NVENC 参数避免低延迟模式，减少花屏风险"""
        args = self.processor._video_encoder_args(
            {"acceleration": {"enabled": True, "mode": "nvidia", "quality": "balanced"}},
            for_subtitles=True,
            encoder="h264_nvenc",
        )

        self.assertEqual(args[args.index("-preset") + 1], "p4")
        self.assertEqual(args[args.index("-cq") + 1], "20")
        self.assertNotIn("ull", args)
        self.assertNotIn("-zerolatency", args)
        self.assertNotIn("-rc-lookahead", args)

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
