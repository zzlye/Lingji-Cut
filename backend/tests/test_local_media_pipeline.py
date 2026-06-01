# backend/tests/test_local_media_pipeline.py
# 本地媒体管线测试 - 不依赖 YouTube 或外部 API，验证 ffmpeg 画面、字幕和导出链路

import os
import shutil
import subprocess
import sys
import tempfile
import unittest


# 嵌入式 Python 默认可能不读取 PYTHONPATH，测试文件直接运行时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.ffmpeg_processor import FFmpegProcessor
from backend.core.subtitle_engine import SubtitleEngine
from backend.core.voice_engine import VoiceEngine


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
        }


if __name__ == "__main__":
    unittest.main()
