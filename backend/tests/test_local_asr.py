# 本地语音识别测试 - 通过 mock faster-whisper 避免真实下载和加载模型

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


# 嵌入式 Python 直接运行测试时需要手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.core.local_asr import LocalSpeechRecognizer, _MODEL_CACHE, cuda_device_count


class FakeSegment:
    """测试用识别片段"""

    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class FakeInfo:
    """测试用识别元信息"""

    language = "en"
    duration = 4.0


class FakeWhisperModel:
    """测试用 Whisper 模型，记录初始化参数和识别参数"""

    init_calls: list[dict] = []
    transcribe_calls: list[dict] = []

    def __init__(self, model_name, **kwargs):
        self.init_calls.append({"model_name": model_name, **kwargs})

    def transcribe(self, video_path, **kwargs):
        self.transcribe_calls.append({"video_path": video_path, **kwargs})
        return [
            FakeSegment(0.0, 1.2, " hello   world "),
            FakeSegment(1.2, 2.5, "本地 字幕"),
        ], FakeInfo()


class FailingGpuWhisperModel(FakeWhisperModel):
    """测试用模型，模拟 CUDA 初始化失败"""

    def __init__(self, model_name, **kwargs):
        super().__init__(model_name, **kwargs)
        if kwargs.get("device") == "cuda":
            raise RuntimeError("CUDA 初始化失败")


class LocalSpeechRecognizerTest(unittest.TestCase):
    """本地语音识别器测试"""

    def setUp(self):
        """创建临时视频文件和清理模型缓存"""
        _MODEL_CACHE.clear()
        cuda_device_count.cache_clear()
        FakeWhisperModel.init_calls.clear()
        FakeWhisperModel.transcribe_calls.clear()
        self.temp_dir = tempfile.mkdtemp(prefix="local_asr_")
        self.video_path = os.path.join(self.temp_dir, "input.mp4")
        with open(self.video_path, "wb") as file:
            file.write(b"fake video")

    def tearDown(self):
        """清理测试文件和模型缓存"""
        _MODEL_CACHE.clear()
        cuda_device_count.cache_clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_transcribe_video_uses_cpu_int8_defaults_and_returns_srt_entries(self):
        """无 GPU 时默认使用 CPU int8 识别，并转换成带时间轴字幕条目"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel
        progress_values: list[float] = []

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path, progress_callback=progress_values.append)

        self.assertEqual(language, "en")
        self.assertEqual(entries[0]["start"], "00:00:00,000")
        self.assertEqual(entries[0]["end"], "00:00:01,200")
        self.assertEqual(entries[0]["text"], "hello world")
        self.assertEqual(entries[1]["text"], "本地 字幕")
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cpu")
        self.assertEqual(FakeWhisperModel.init_calls[0]["compute_type"], "int8")
        self.assertEqual(FakeWhisperModel.init_calls[0]["cpu_threads"], 2)
        self.assertTrue(FakeWhisperModel.transcribe_calls[0]["vad_filter"])
        self.assertEqual(progress_values[-1], 100)

    def test_transcribe_video_prefers_gpu_when_cuda_is_available(self):
        """有 CUDA 时默认优先使用 GPU float16，保证识别速度"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=1),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path)

        self.assertEqual(language, "en")
        self.assertTrue(entries)
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cuda")
        self.assertEqual(FakeWhisperModel.init_calls[0]["compute_type"], "float16")

    def test_transcribe_video_falls_back_to_cpu_when_auto_gpu_fails(self):
        """自动 GPU 识别失败时回退 CPU，兼容没有完整 CUDA 环境的电脑"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FailingGpuWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=1),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path)

        self.assertEqual(language, "en")
        self.assertTrue(entries)
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cuda")
        self.assertEqual(FakeWhisperModel.init_calls[1]["device"], "cpu")
        self.assertEqual(FakeWhisperModel.init_calls[1]["compute_type"], "int8")

    def test_missing_video_raises_clear_error(self):
        """视频文件不存在时给出本地识别错误"""
        recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir)

        with self.assertRaisesRegex(FileNotFoundError, "本地识别视频不存在"):
            recognizer.transcribe_video(os.path.join(self.temp_dir, "missing.mp4"))


if __name__ == "__main__":
    unittest.main()
