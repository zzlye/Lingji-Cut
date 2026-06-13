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

from backend.core.local_asr import LocalSpeechRecognizer, _MODEL_CACHE, cuda_device_count, cuda_free_memory_mib, cuda_memory_mib


class FakeSegment:
    """测试用识别片段"""

    def __init__(self, start: float, end: float, text: str, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words or []


class FakeWord:
    """测试用词级时间戳"""

    def __init__(self, start: float, end: float, word: str):
        self.start = start
        self.end = end
        self.word = word


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
        cuda_memory_mib.cache_clear()
        cuda_free_memory_mib.cache_clear()
        FakeWhisperModel.init_calls.clear()
        FakeWhisperModel.transcribe_calls.clear()
        # 测试中关闭音频预处理，避免真实调用 ffmpeg
        self.env_patcher = patch.dict(os.environ, {"YTV_ASR_PREPROCESS": "0"})
        self.env_patcher.start()
        self.temp_dir = tempfile.mkdtemp(prefix="local_asr_")
        self.video_path = os.path.join(self.temp_dir, "input.mp4")
        with open(self.video_path, "wb") as file:
            file.write(b"fake video")

    def tearDown(self):
        """清理测试文件和模型缓存"""
        self.env_patcher.stop()
        _MODEL_CACHE.clear()
        cuda_device_count.cache_clear()
        cuda_memory_mib.cache_clear()
        cuda_free_memory_mib.cache_clear()
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
        self.assertEqual(FakeWhisperModel.init_calls[0]["model_name"], "base")
        self.assertEqual(FakeWhisperModel.init_calls[0]["compute_type"], "int8")
        self.assertEqual(FakeWhisperModel.init_calls[0]["cpu_threads"], 2)
        self.assertTrue(FakeWhisperModel.transcribe_calls[0]["vad_filter"])
        self.assertLessEqual(FakeWhisperModel.transcribe_calls[0]["vad_parameters"]["threshold"], 0.35)
        self.assertLessEqual(FakeWhisperModel.transcribe_calls[0]["vad_parameters"]["min_speech_duration_ms"], 120)
        self.assertLessEqual(FakeWhisperModel.transcribe_calls[0]["no_speech_threshold"], 0.85)
        self.assertTrue(FakeWhisperModel.transcribe_calls[0]["word_timestamps"])
        self.assertEqual(FakeWhisperModel.transcribe_calls[0]["beam_size"], 3)
        # 必须启用抗幻觉静音阈值，避免长静音里出现幻觉字幕
        self.assertGreaterEqual(FakeWhisperModel.transcribe_calls[0]["hallucination_silence_threshold"], 0.5)
        # 垫片不能太大，否则字幕会比声音早出
        self.assertLessEqual(FakeWhisperModel.transcribe_calls[0]["vad_parameters"]["speech_pad_ms"], 250)
        self.assertEqual(progress_values[-1], 100)

    def test_transcribe_video_prefers_gpu_when_cuda_is_available(self):
        """有 CUDA 时默认优先使用 GPU float16，保证识别速度"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=1),
            patch("backend.core.local_asr.cuda_memory_mib", return_value=4096),
            patch("backend.core.local_asr.cuda_free_memory_mib", return_value=2500),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path)

        self.assertEqual(language, "en")
        self.assertTrue(entries)
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cuda")
        self.assertEqual(FakeWhisperModel.init_calls[0]["model_name"], "small")
        self.assertEqual(FakeWhisperModel.init_calls[0]["compute_type"], "float16")
        self.assertEqual(FakeWhisperModel.transcribe_calls[0]["beam_size"], 5)

    def test_transcribe_video_uses_medium_model_on_large_gpu(self):
        """显存足够时 GPU 默认使用 medium，提高专业词识别准确率"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=1),
            patch("backend.core.local_asr.cuda_memory_mib", return_value=8192),
            patch("backend.core.local_asr.cuda_free_memory_mib", return_value=6000),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path)

        self.assertEqual(language, "en")
        self.assertTrue(entries)
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cuda")
        self.assertEqual(FakeWhisperModel.init_calls[0]["model_name"], "medium")

    def test_transcribe_video_falls_back_to_cpu_when_auto_gpu_fails(self):
        """自动 GPU 识别失败时回退 CPU，兼容没有完整 CUDA 环境的电脑"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FailingGpuWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=1),
            patch("backend.core.local_asr.cuda_memory_mib", return_value=4096),
            patch("backend.core.local_asr.cuda_free_memory_mib", return_value=2500),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, language = recognizer.transcribe_video(self.video_path)

        self.assertEqual(language, "en")
        self.assertTrue(entries)
        self.assertEqual(FakeWhisperModel.init_calls[0]["device"], "cuda")
        self.assertEqual(FakeWhisperModel.init_calls[0]["model_name"], "small")
        self.assertEqual(FakeWhisperModel.init_calls[1]["device"], "cpu")
        self.assertEqual(FakeWhisperModel.init_calls[1]["model_name"], "base")
        self.assertEqual(FakeWhisperModel.init_calls[1]["compute_type"], "int8")

    def test_missing_video_raises_clear_error(self):
        """视频文件不存在时给出本地识别错误"""
        recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir)

        with self.assertRaisesRegex(FileNotFoundError, "本地识别视频不存在"):
            recognizer.transcribe_video(os.path.join(self.temp_dir, "missing.mp4"))

    def test_word_timestamps_split_long_segment_with_word_times(self):
        """词级时间戳可把长识别段切成更贴近语音边界的短字幕"""
        fake_module = types.ModuleType("faster_whisper")

        class WordWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    FakeSegment(0.0, 4.0, "ignored", words=[
                        FakeWord(0.0, 0.4, "这是"),
                        FakeWord(0.4, 0.8, "一个"),
                        FakeWord(0.8, 1.2, "比较"),
                        FakeWord(1.2, 1.6, "长的"),
                        FakeWord(1.6, 2.0, "本地"),
                        FakeWord(2.0, 2.4, "识别"),
                        FakeWord(2.4, 2.8, "字幕，"),
                        FakeWord(2.8, 3.2, "后半段"),
                        FakeWord(3.2, 3.6, "继续"),
                        FakeWord(3.6, 4.0, "播放。"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = WordWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertGreater(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:00,000")
        self.assertEqual(entries[0]["end"], "00:00:02,800")
        self.assertEqual(entries[1]["start"], "00:00:02,800")

    def test_word_timestamps_split_on_long_pause_even_for_short_text(self):
        """短句后有明显停顿时也要断开，避免字幕挂到下一句话已经开始之后"""
        fake_module = types.ModuleType("faster_whisper")

        class PauseWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    FakeSegment(0.0, 3.5, "ignored", words=[
                        FakeWord(0.0, 0.5, "どう?"),
                        FakeWord(2.4, 3.0, "次です"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = PauseWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["end"], "00:00:00,500")
        self.assertEqual(entries[1]["start"], "00:00:02,400")

    def test_word_timestamps_avoid_splitting_short_japanese_tail(self):
        """词级硬断不会把日语短词尾单独甩到下一条字幕"""
        fake_module = types.ModuleType("faster_whisper")

        class JapaneseTailWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    FakeSegment(14.5, 21.5, "ignored", words=[
                        FakeWord(14.5, 15.0, "1日の"),
                        FakeWord(15.0, 15.5, "朝対"),
                        FakeWord(15.5, 16.0, "朝の"),
                        FakeWord(16.0, 16.5, "時間対"),
                        FakeWord(16.5, 17.0, "激しく"),
                        FakeWord(17.0, 17.5, "降る"),
                        FakeWord(17.5, 18.0, "時間が"),
                        FakeWord(18.0, 18.5, "出てくる"),
                        FakeWord(18.5, 19.0, "かなといった"),
                        FakeWord(19.0, 19.5, "状況に"),
                        FakeWord(19.5, 20.0, "な"),
                        FakeWord(20.0, 20.5, "りそうです"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = JapaneseTailWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertEqual(len(entries), 1)
        self.assertIn("なりそうです", entries[0]["text"])

    def test_transcribe_video_merges_too_short_orphan_entries(self):
        """极短的英文孤立词和词尾应并回相邻字幕，避免时间轴忽快忽慢"""
        fake_module = types.ModuleType("faster_whisper")

        class ShortOrphanWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    FakeSegment(72.54, 74.14, "ignored", words=[
                        FakeWord(72.54, 72.88, " allowing"),
                        FakeWord(72.88, 73.10, " you"),
                        FakeWord(73.10, 73.24, " to"),
                        FakeWord(73.24, 73.56, " effectively"),
                        FakeWord(73.56, 73.74, " hit"),
                        FakeWord(73.74, 73.98, " through"),
                        FakeWord(73.98, 74.14, " someone's"),
                    ]),
                    FakeSegment(74.14, 74.40, "ignored", words=[
                        FakeWord(74.14, 74.40, " shield."),
                    ]),
                    FakeSegment(114.86, 115.06, "ignored", words=[
                        FakeWord(114.86, 115.06, " A"),
                    ]),
                    FakeSegment(115.04, 117.56, "ignored", words=[
                        FakeWord(115.04, 115.30, " density"),
                        FakeWord(115.30, 115.48, " mace"),
                        FakeWord(115.48, 115.62, " is"),
                        FakeWord(115.62, 115.80, " used"),
                        FakeWord(115.80, 116.00, " for"),
                        FakeWord(116.00, 116.38, " regular"),
                        FakeWord(116.38, 116.62, " macing"),
                        FakeWord(116.62, 116.84, " from"),
                        FakeWord(116.84, 117.06, " high"),
                        FakeWord(117.06, 117.56, " distances,"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = ShortOrphanWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        texts = [entry["text"] for entry in entries]
        self.assertNotIn("shield.", texts)
        self.assertNotIn("A", texts)
        self.assertIn("allowing you to effectively hit through someone's shield.", texts)
        self.assertIn("A density mace is used for regular macing from high distances,", texts)
        merged_shield = next(entry for entry in entries if entry["text"].endswith("shield."))
        merged_density = next(entry for entry in entries if entry["text"].startswith("A density mace"))
        self.assertEqual(merged_shield["end"], "00:01:14,400")
        self.assertEqual(merged_density["start"], "00:01:54,860")


    def test_word_timestamps_cap_overlong_single_word(self):
        """背景音乐导致单词时间被拉长时，按字数封顶避免字幕滞留"""
        fake_module = types.ModuleType("faster_whisper")

        class StretchedWordWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    # "god" 实际发音不到 1 秒，却被对齐拉长到 3.2 秒
                    FakeSegment(10.31, 13.51, "ignored", words=[
                        FakeWord(10.31, 13.51, " god"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = StretchedWordWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["start"], "00:00:10,310")
        # 3 个字符的词最长 0.6 + 0.15*3 = 1.05 秒
        self.assertEqual(entries[0]["end"], "00:00:11,360")

    def test_word_timestamps_fix_backward_word_start(self):
        """词开始时间倒退到上一个词之前时贴回边界，避免字幕提前出现"""
        fake_module = types.ModuleType("faster_whisper")

        class BackwardWordWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                return [
                    FakeSegment(5.0, 8.0, "ignored", words=[
                        FakeWord(5.0, 6.0, " hello"),
                        # 开始时间异常回退到 4.2，应贴回 6.0
                        FakeWord(4.2, 7.0, " world"),
                    ]),
                ], FakeInfo()

        fake_module.WhisperModel = BackwardWordWhisperModel
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertEqual(entries[0]["start"], "00:00:05,000")
        self.assertEqual(entries[-1]["end"], "00:00:07,000")

    def test_prepare_audio_falls_back_to_original_when_ffmpeg_fails(self):
        """音频预处理失败时回退用原始视频识别，不影响整体流程"""
        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel

        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch.dict(os.environ, {"YTV_ASR_PREPROCESS": "1"}),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
            patch("backend.core.local_asr.get_ffmpeg_command", return_value="ffmpeg-not-exists.exe"),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertTrue(entries)
        # 预处理失败时识别输入必须回退到原始视频路径
        self.assertEqual(FakeWhisperModel.transcribe_calls[0]["video_path"], self.video_path)

    def test_gap_rescue_recovers_quiet_speech_dropped_by_vad(self):
        """VAD 检测到有语音但字幕没覆盖的漏区关闭 VAD 补漏，找回轻声细语并过滤幻觉字幕"""
        fake_module = types.ModuleType("faster_whisper")

        class QuietSegment(FakeSegment):
            """测试用补漏片段，携带置信度信息"""

            def __init__(self, start, end, text, words=None, no_speech_prob=0.1, avg_logprob=-0.5):
                super().__init__(start, end, text, words)
                self.no_speech_prob = no_speech_prob
                self.avg_logprob = avg_logprob
                self.compression_ratio = 1.2

        class GapRescueWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                if kwargs.get("vad_filter"):
                    # 第一遍主识别只覆盖 0-1 秒，2 秒后的轻声被主识别漏掉
                    return [
                        FakeSegment(0.0, 1.0, "ignored", words=[FakeWord(0.0, 1.0, "正常音量")]),
                    ], FakeInfo()
                # 第二遍补漏(关 VAD)：clip 从漏区前留 0.3 秒上下文起算，返回相对时间
                return [
                    QuietSegment(0.3, 1.3, "ささやき", words=[FakeWord(0.3, 1.3, "ささやき")]),
                    QuietSegment(1.5, 2.0, "ご視聴ありがとうございました", no_speech_prob=0.1),
                    QuietSegment(2.1, 2.3, "音楽幻覚", no_speech_prob=0.9),
                ], FakeInfo()

        fake_module.WhisperModel = GapRescueWhisperModel
        fake_audio_module = types.ModuleType("faster_whisper.audio")
        fake_audio_module.decode_audio = lambda path, sampling_rate=16000: [0.0] * (sampling_rate * 4)
        fake_vad_module = types.ModuleType("faster_whisper.vad")
        fake_vad_module.VadOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
        # VAD 检测到 0-1 秒和 2-3 秒两段语音，后一段没字幕覆盖应触发补漏
        fake_vad_module.get_speech_timestamps = lambda audio, options: [
            {"start": 0, "end": 16000},
            {"start": 32000, "end": 48000},
        ]

        with (
            patch.dict(sys.modules, {
                "faster_whisper": fake_module,
                "faster_whisper.audio": fake_audio_module,
                "faster_whisper.vad": fake_vad_module,
            }),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        texts = [entry["text"] for entry in entries]
        # 轻声字幕被找回，相对 0.3 秒平移回漏区起点 2.0 秒
        self.assertIn("ささやき", texts)
        rescued = next(entry for entry in entries if entry["text"] == "ささやき")
        self.assertEqual(rescued["start"], "00:00:02,000")
        # 幻觉句式和高静音概率的片段必须被过滤
        self.assertNotIn("ご視聴ありがとうございました", texts)
        self.assertNotIn("音楽幻覚", texts)
        # 补漏调用必须关闭 VAD
        rescue_call = GapRescueWhisperModel.transcribe_calls[-1]
        self.assertFalse(rescue_call["vad_filter"])


    def test_vad_calibration_fixes_late_and_dragging_subtitle(self):
        """词级对齐偏慢的单音节字幕按 VAD 语音边界回贴，拖尾也会收回"""
        fake_module = types.ModuleType("faster_whisper")

        class LateWordWhisperModel(FakeWhisperModel):
            def transcribe(self, video_path, **kwargs):
                self.transcribe_calls.append({"video_path": video_path, **kwargs})
                if not kwargs.get("vad_filter"):
                    # 补漏阶段(关 VAD)不返回内容，本用例专注验证边界校准
                    return [], FakeInfo()
                return [
                    # 实际语音 0.3 秒就开始了，词级对齐却从 1.0 秒才开始
                    FakeSegment(1.0, 2.8, "ignored", words=[FakeWord(1.0, 2.8, "なんでですかね?")]),
                ], FakeInfo()

        fake_module.WhisperModel = LateWordWhisperModel
        fake_audio_module = types.ModuleType("faster_whisper.audio")
        fake_audio_module.decode_audio = lambda path, sampling_rate=16000: [0.0] * (sampling_rate * 4)
        fake_vad_module = types.ModuleType("faster_whisper.vad")

        class FakeVadOptions:
            """测试用 VAD 配置"""

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_vad_module.VadOptions = FakeVadOptions
        # VAD 检测到的真实语音区间是 0.3 - 1.6 秒
        fake_vad_module.get_speech_timestamps = lambda audio, options: [{"start": 4800, "end": 25600}]

        with (
            patch.dict(sys.modules, {
                "faster_whisper": fake_module,
                "faster_whisper.audio": fake_audio_module,
                "faster_whisper.vad": fake_vad_module,
            }),
            patch("backend.core.local_asr.cuda_device_count", return_value=0),
        ):
            recognizer = LocalSpeechRecognizer(model_dir=self.temp_dir, cpu_threads=2)
            entries, _ = recognizer.transcribe_video(self.video_path)

        self.assertEqual(len(entries), 1)
        # 开始时间贴回语音边界 0.3 + 0.05
        self.assertEqual(entries[0]["start"], "00:00:00,350")
        # 结尾收回到语音结束 1.6 + 0.25
        self.assertEqual(entries[0]["end"], "00:00:01,850")


if __name__ == "__main__":
    unittest.main()
