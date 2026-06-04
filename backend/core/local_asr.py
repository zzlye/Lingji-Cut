# backend/core/local_asr.py
# 本地语音识别 - 使用 faster-whisper 在本机从视频音频生成带时间轴字幕

import os
import subprocess
from functools import lru_cache
from typing import Any, Callable, Optional

from ..utils import get_logger


logger = get_logger("local_asr")

# 模型缓存，避免同一轮任务重复加载模型。
_MODEL_CACHE: dict[tuple[str, str, str, str, int], object] = {}


def default_asr_model_dir() -> str:
    """返回本地 Whisper 模型目录，默认放在 D:\\tools，避免污染项目目录"""
    tools_dir = os.environ.get("YTV_TOOLS_DIR") or "D:\\tools"
    return os.environ.get("YTV_ASR_MODEL_DIR") or os.path.join(tools_dir, "whisper-models")


def default_asr_model_name() -> str:
    """低配机器默认用 base，速度和准确率比 tiny 更均衡"""
    return os.environ.get("YTV_ASR_MODEL") or "base"


@lru_cache(maxsize=1)
def cuda_memory_mib() -> int:
    """读取第一块 CUDA 显卡显存，用于自动选择本地识别模型大小"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0
    first_line = (result.stdout or "").splitlines()[0:1]
    if not first_line:
        return 0
    try:
        return int(float(first_line[0].strip().split()[0]))
    except (ValueError, IndexError):
        return 0


def default_gpu_asr_model_name() -> str:
    """按 GPU 显存选择更准的模型，避免低显存机器被大模型拖垮"""
    memory_mib = cuda_memory_mib()
    if memory_mib >= 7000:
        return "medium"
    if memory_mib >= 3500:
        return "small"
    return "base"


@lru_cache(maxsize=1)
def cuda_device_count() -> int:
    """返回 CTranslate2 可用的 CUDA 设备数量"""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def default_asr_device() -> str:
    """默认优先 GPU，没有 CUDA 时自动回退 CPU"""
    requested = (os.environ.get("YTV_ASR_DEVICE") or "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    return "cuda" if cuda_device_count() > 0 else "cpu"


def default_asr_compute_type(device: str) -> str:
    """根据设备选择默认计算精度，兼顾速度和兼容性"""
    configured = os.environ.get("YTV_ASR_COMPUTE_TYPE")
    if configured:
        return configured
    return "float16" if device == "cuda" else "int8"


class LocalSpeechRecognizer:
    """本地语音识别器，默认优先 GPU，没有 GPU 时回退 CPU"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        model_dir: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        cpu_threads: Optional[int] = None,
    ):
        """初始化本地识别参数，不在构造阶段加载模型"""
        self.model_dir = model_dir or default_asr_model_dir()
        requested_device = device or os.environ.get("YTV_ASR_DEVICE") or "auto"
        normalized_device = str(requested_device).strip().lower()
        self.allow_cpu_fallback = normalized_device == "auto"
        self.device = default_asr_device() if normalized_device == "auto" else normalized_device
        configured_model = model_name or os.environ.get("YTV_ASR_MODEL")
        self.auto_model_name = not configured_model
        # 有 GPU 时按显存提升模型准确率，CPU 仍用 base，兼顾准确率和中低配可用性。
        self.model_name = configured_model or (default_gpu_asr_model_name() if self.device == "cuda" else default_asr_model_name())
        self.compute_type = compute_type or default_asr_compute_type(self.device)
        self.cpu_threads = cpu_threads or min(4, max(1, os.cpu_count() or 1))

    def transcribe_video(
        self,
        video_path: str,
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[dict], str]:
        """识别视频音频并返回字幕条目和检测到的语言"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"本地识别视频不存在: {video_path}")

        try:
            segments, info = self._transcribe_with_model(video_path, language, progress_callback)
        except Exception as exc:
            if not self._should_fallback_to_cpu():
                raise
            logger.warning(f"GPU 本地识别失败，自动回退 CPU: {exc}")
            self.device = "cpu"
            if self.auto_model_name:
                self.model_name = default_asr_model_name()
            self.compute_type = os.environ.get("YTV_ASR_CPU_COMPUTE_TYPE") or "int8"
            segments, info = self._transcribe_with_model(video_path, language, progress_callback)

        detected_language = str(getattr(info, "language", "") or language or "auto")
        duration = float(getattr(info, "duration", 0) or 0)
        entries: list[dict] = []
        for segment in segments:
            segment_entries = self._segment_to_entries(segment)
            for entry in segment_entries:
                if not str(entry.get("text") or "").strip():
                    continue
                entry["index"] = len(entries) + 1
                entries.append(entry)
            if progress_callback and duration > 0:
                end = max(
                    (self._srt_time_to_seconds(str(entry.get("end", "00:00:00,000"))) for entry in segment_entries),
                    default=float(getattr(segment, "end", 0) or 0),
                )
                progress_callback(min(95.0, 5.0 + end / duration * 90.0))

        if progress_callback:
            progress_callback(100)
        if not entries:
            raise RuntimeError("本地语音识别没有识别到字幕内容")
        logger.info(f"本地识别完成: {len(entries)} 条字幕, language={detected_language}")
        return entries, detected_language

    def _transcribe_with_model(
        self,
        video_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ):
        """加载模型并执行一次识别"""
        model = self._load_model()
        logger.info(f"本地识别字幕: model={self.model_name}, device={self.device}, compute={self.compute_type}")
        if progress_callback:
            progress_callback(5)

        return model.transcribe(
            video_path,
            language=language or None,
            vad_filter=True,
            word_timestamps=True,
            beam_size=self._beam_size(),
            condition_on_previous_text=False,
        )

    def _segment_to_entries(self, segment: Any) -> list[dict]:
        """优先用词级时间戳切分字幕，没有词时间戳时退回整段时间"""
        words = [word for word in (getattr(segment, "words", None) or []) if str(getattr(word, "word", "") or "").strip()]
        if not words:
            return [self._segment_fallback_entry(segment)]

        entries: list[dict] = []
        current: list[Any] = []
        max_chars = 28
        hard_limit = 42
        min_punctuation_chars = 12
        max_duration = 6.5
        pause_seconds = 0.35
        break_chars = "，。、！？；,.!?;"
        for index, word in enumerate(words):
            current.append(word)
            text = self._words_text(current)
            if not text:
                current.clear()
                continue
            next_word = words[index + 1] if index + 1 < len(words) else None
            should_break = self._should_break_words(
                current,
                text,
                next_word,
                break_chars,
                max_chars,
                hard_limit,
                min_punctuation_chars,
                max_duration,
                pause_seconds,
            )
            if should_break:
                entries.append(self._words_to_entry(current))
                current = []

        if current:
            entries.append(self._words_to_entry(current))
        return entries or [self._segment_fallback_entry(segment)]

    def _segment_fallback_entry(self, segment: Any) -> dict:
        """把 Whisper 整段结果转换成字幕条目"""
        text = " ".join(str(getattr(segment, "text", "") or "").split())
        start = float(getattr(segment, "start", 0) or 0)
        end = float(getattr(segment, "end", start) or start)
        return {
            "index": 0,
            "start": self._seconds_to_srt_time(start),
            "end": self._seconds_to_srt_time(max(end, start + 0.2)),
            "text": text,
        }

    def _words_to_entry(self, words: list[Any]) -> dict:
        """把一组词转换成更贴近语音边界的字幕条目"""
        start = float(getattr(words[0], "start", 0) or 0)
        end = float(getattr(words[-1], "end", start) or start)
        return {
            "index": 0,
            "start": self._seconds_to_srt_time(start),
            "end": self._seconds_to_srt_time(max(end, start + 0.2)),
            "text": self._words_text(words),
        }

    def _should_break_words(
        self,
        words: list[Any],
        text: str,
        next_word: Any,
        break_chars: str,
        max_chars: int,
        hard_limit: int,
        min_punctuation_chars: int,
        max_duration: float,
        pause_seconds: float,
    ) -> bool:
        """判断词级字幕是否应该断句，避免把日语词尾单独切到下一条"""
        if not next_word:
            return False
        start = float(getattr(words[0], "start", 0) or 0)
        end = float(getattr(words[-1], "end", start) or start)
        next_start = float(getattr(next_word, "start", end) or end)
        next_text = str(getattr(next_word, "word", "") or "").strip()
        duration = max(0.0, end - start)
        pause = max(0.0, next_start - end)
        if text[-1] in break_chars and len(text) >= min_punctuation_chars:
            return True
        if len(text) < max_chars and duration < max_duration:
            return False
        if pause >= pause_seconds:
            return True
        if len(text) >= hard_limit:
            return not self._is_short_japanese_tail(next_text)
        return duration >= max_duration and not self._is_short_japanese_tail(next_text)

    def _is_short_japanese_tail(self, text: str) -> bool:
        """识别日语短词尾，硬断时尽量和前一句保持在同一条字幕里"""
        normalized = text.strip(" 　、。,.!?！？；;")
        return normalized in {
            "です",
            "ます",
            "した",
            "して",
            "ますね",
            "ですね",
            "なんですね",
            "ります",
            "りそう",
            "そうです",
            "ています",
            "ている",
            "けれども",
            "ですけれども",
        }

    def _words_text(self, words: list[Any]) -> str:
        """合并词级文本，兼容英文前导空格和中日韩无空格文本"""
        raw = "".join(str(getattr(word, "word", "") or "") for word in words)
        return " ".join(raw.split())

    def _beam_size(self) -> int:
        """读取识别 beam size，GPU 默认更准，CPU 默认更快"""
        configured = os.environ.get("YTV_ASR_BEAM_SIZE")
        if configured:
            try:
                return max(1, min(8, int(configured)))
            except ValueError:
                pass
        return 5 if self.device == "cuda" else 3

    def _should_fallback_to_cpu(self) -> bool:
        """判断当前识别失败时是否允许回退 CPU"""
        return self.allow_cpu_fallback and self.device == "cuda"

    def _load_model(self):
        """延迟加载 faster-whisper 模型，缺依赖时给出明确提示"""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("缺少本地识别依赖 faster-whisper，请先安装到 D:\\tools 的 Python 环境") from exc

        key = (self.model_name, self.model_dir, self.device, self.compute_type, self.cpu_threads)
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        os.makedirs(self.model_dir, exist_ok=True)
        model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            download_root=self.model_dir,
        )
        _MODEL_CACHE[key] = model
        return model

    def _seconds_to_srt_time(self, seconds: float) -> str:
        """把秒转换成 SRT 时间码"""
        milliseconds = max(0, int(round(seconds * 1000)))
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        secs = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _srt_time_to_seconds(self, value: str) -> float:
        """把 SRT 时间码转换成秒，供进度估算使用"""
        parts = value.replace(",", ".").split(":")
        if len(parts) != 3:
            return 0.0
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
