# backend/core/local_asr.py
# 本地语音识别 - 使用 faster-whisper 在本机从视频音频生成带时间轴字幕

import os
import re
import subprocess
import tempfile
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Callable, Optional

from .tooling import get_ffmpeg_command
from ..utils import get_logger


logger = get_logger("local_asr")
TERMINAL_PUNCTUATION = "，。、！？；,.!?;:："
LEADING_FRAGMENT_WORDS = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "with",
    "and",
    "or",
    "but",
    "so",
    "if",
    "when",
    "while",
    "that",
    "this",
    "these",
    "those",
}

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

        # 预处理音频：响度归一化让低声说话更容易被识别，时间轴保持不变。
        audio_path, temp_audio_path = self._prepare_audio(video_path)
        try:
            try:
                segments, info = self._transcribe_with_model(audio_path, language, progress_callback)
            except Exception as exc:
                if not self._should_fallback_to_cpu():
                    raise
                logger.warning(f"GPU 本地识别失败，自动回退 CPU: {exc}")
                self.device = "cpu"
                if self.auto_model_name:
                    self.model_name = default_asr_model_name()
                self.compute_type = os.environ.get("YTV_ASR_CPU_COMPUTE_TYPE") or "int8"
                segments, info = self._transcribe_with_model(audio_path, language, progress_callback)

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
        finally:
            # 识别结束后清理预处理生成的临时音频
            if temp_audio_path:
                try:
                    os.remove(temp_audio_path)
                except OSError:
                    pass

        if progress_callback:
            progress_callback(100)
        if not entries:
            raise RuntimeError("本地语音识别没有识别到字幕内容")
        entries = self._merge_short_entries(entries)
        for index, entry in enumerate(entries, 1):
            entry["index"] = index
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
            vad_filter=self._vad_filter_enabled(),
            vad_parameters=self._vad_parameters(),
            word_timestamps=True,
            beam_size=self._beam_size(),
            no_speech_threshold=self._no_speech_threshold(),
            hallucination_silence_threshold=self._hallucination_silence_threshold(),
            condition_on_previous_text=False,
        )

    def _prepare_audio(self, video_path: str) -> tuple[str, Optional[str]]:
        """提取 16k 单声道音频并做响度归一化，返回(识别用路径, 待清理临时文件)"""
        if not self._preprocess_enabled():
            return video_path, None

        temp_path = ""
        try:
            fd, temp_path = tempfile.mkstemp(prefix="ytv_asr_", suffix=".wav")
            os.close(fd)
            result = subprocess.run(
                [
                    get_ffmpeg_command(),
                    "-y",
                    "-i", video_path,
                    "-vn",
                    "-ac", "1",
                    "-ar", "16000",
                    "-af", self._audio_filter(),
                    "-c:a", "pcm_s16le",
                    temp_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            if result.returncode == 0 and os.path.getsize(temp_path) > 44:
                logger.info("本地识别音频预处理完成，已做人声响度归一化")
                return temp_path, temp_path
            logger.warning(f"音频预处理失败(code={result.returncode})，使用原始视频识别")
        except Exception as exc:
            logger.warning(f"音频预处理异常，使用原始视频识别: {exc}")
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return video_path, None

    def _preprocess_enabled(self) -> bool:
        """读取音频预处理开关，默认开启以提升低声说话的识别率"""
        configured = str(os.environ.get("YTV_ASR_PREPROCESS") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _audio_filter(self) -> str:
        """识别前的音频滤镜：滤掉低频隆隆声并做响度归一化，把低声说话抬到可识别响度"""
        return os.environ.get("YTV_ASR_AUDIO_FILTER") or "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"

    def _hallucination_silence_threshold(self) -> float:
        """静音超过该秒数时跳过其中的幻觉词，避免字幕挂在没有人声的位置"""
        return self._env_float("YTV_ASR_HALLUCINATION_SILENCE", 2.0, 0.5, 10.0)

    def _segment_to_entries(self, segment: Any) -> list[dict]:
        """优先用词级时间戳切分字幕，没有词时间戳时退回整段时间"""
        words = [word for word in (getattr(segment, "words", None) or []) if str(getattr(word, "word", "") or "").strip()]
        if not words:
            return [self._segment_fallback_entry(segment)]
        words = self._sanitize_words(words)

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

    def _sanitize_words(self, words: list[Any]) -> list[Any]:
        """钳制词级时间戳，纠正背景音乐导致的单词拉长和时间倒退"""
        sanitized: list[Any] = []
        prev_end: Optional[float] = None
        for word in words:
            text = str(getattr(word, "word", "") or "")
            start = float(getattr(word, "start", 0) or 0)
            end = float(getattr(word, "end", start) or start)
            # 词开始时间早于上一个词结尾时，贴回上一个词的结尾，避免字幕提前出现
            if prev_end is not None and start < prev_end:
                start = prev_end
            if end < start:
                end = start
            # 单词被对齐算法拉长时按字数封顶，避免字幕在声音结束后仍然滞留
            max_duration = self._max_word_duration(text)
            if end - start > max_duration:
                end = start + max_duration
            sanitized.append(SimpleNamespace(word=text, start=start, end=end))
            prev_end = end
        return sanitized

    def _max_word_duration(self, text: str) -> float:
        """按字符数估算单个词的最长合理发音时长"""
        chars = len(text.strip())
        return min(3.0, 0.6 + 0.15 * max(1, chars))

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
        if pause >= pause_seconds and not self._is_short_japanese_tail(next_text):
            return True
        if len(text) < max_chars and duration < max_duration:
            return False
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

    def _merge_short_entries(self, entries: list[dict]) -> list[dict]:
        """合并极短字幕碎片，避免单词或词尾独立闪现导致时间轴观感发飘"""
        if len(entries) < 2:
            return entries

        merged = [dict(entry) for entry in entries]
        index = 0
        max_duration_ms = 350
        short_tail_duration_ms = 900
        max_gap_ms = 120
        while index < len(merged):
            current = merged[index]
            prev_entry = merged[index - 1] if index > 0 else None
            next_entry = merged[index + 1] if index + 1 < len(merged) else None
            duration_ms = self._entry_duration_ms(current)
            continuation_tail = self._is_short_continuation(prev_entry, current, duration_ms, short_tail_duration_ms)
            if (duration_ms > max_duration_ms and not continuation_tail) or not self._is_merge_candidate_text(str(current.get("text") or "")):
                index += 1
                continue

            prev_gap = self._entry_gap_ms(prev_entry, current)
            next_gap = self._entry_gap_ms(current, next_entry)
            merge_forward = next_entry is not None and next_gap <= max_gap_ms
            merge_backward = prev_entry is not None and prev_gap <= max_gap_ms
            if merge_forward and not continuation_tail and (self._prefer_merge_forward(str(current.get("text") or "")) or not merge_backward):
                next_entry["start"] = current["start"]
                next_entry["text"] = self._join_entry_text(str(current.get("text") or ""), str(next_entry.get("text") or ""))
                merged.pop(index)
                continue
            if merge_backward:
                prev_entry["end"] = current["end"]
                prev_entry["text"] = self._join_entry_text(str(prev_entry.get("text") or ""), str(current.get("text") or ""))
                merged.pop(index)
                index = max(0, index - 1)
                continue
            index += 1
        return merged

    def _is_merge_candidate_text(self, text: str) -> bool:
        """判断是否属于需要平滑的超短字幕内容"""
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return False
        words = normalized.split()
        compact = normalized.strip(TERMINAL_PUNCTUATION + "\"'()[]{}“”‘’")
        return len(words) <= 2 or len(compact) <= 2

    def _is_short_continuation(self, prev_entry: Optional[dict], current: dict, duration_ms: int, max_duration_ms: int) -> bool:
        """识别上一句没收完就被拆出去的短尾巴，优先并回上一条字幕"""
        if not prev_entry or duration_ms > max_duration_ms:
            return False
        prev_text = " ".join(str(prev_entry.get("text") or "").split()).strip()
        current_text = " ".join(str(current.get("text") or "").split()).strip()
        if not prev_text or not current_text:
            return False
        if prev_text[-1] in TERMINAL_PUNCTUATION:
            return False
        return len(current_text.split()) <= 2

    def _prefer_merge_forward(self, text: str) -> bool:
        """冠词、介词等前导碎片优先并到后一条，词尾则并回前一条"""
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized:
            return False
        if normalized[-1] in TERMINAL_PUNCTUATION:
            return False
        lowered = normalized.strip("\"'()[]{}“”‘’").lower()
        if lowered in LEADING_FRAGMENT_WORDS:
            return True
        return lowered.isalpha() and len(lowered.split()) == 1 and len(lowered) <= 3

    def _join_entry_text(self, left: str, right: str) -> str:
        """合并相邻字幕文本，英文保留空格，中日韩连续文本不额外插空格"""
        left_clean = " ".join(str(left or "").split())
        right_clean = " ".join(str(right or "").split())
        if not left_clean:
            return right_clean
        if not right_clean:
            return left_clean
        if self._is_cjk_tail(left_clean[-1]) and self._is_cjk_head(right_clean[0]):
            return f"{left_clean}{right_clean}"
        if right_clean[0] in TERMINAL_PUNCTUATION:
            return f"{left_clean}{right_clean}"
        return f"{left_clean} {right_clean}"

    def _entry_duration_ms(self, entry: dict) -> int:
        """读取字幕条目时长，统一按毫秒比较阈值"""
        start_ms = int(round(self._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000")) * 1000))
        end_ms = int(round(self._srt_time_to_seconds(str(entry.get("end") or "00:00:00,000")) * 1000))
        return max(0, end_ms - start_ms)

    def _entry_gap_ms(self, left: Optional[dict], right: Optional[dict]) -> int:
        """计算相邻字幕之间的间隔，重叠时按 0 处理，方便平滑边界碎片"""
        if not left or not right:
            return 10**9
        left_end = int(round(self._srt_time_to_seconds(str(left.get("end") or "00:00:00,000")) * 1000))
        right_start = int(round(self._srt_time_to_seconds(str(right.get("start") or "00:00:00,000")) * 1000))
        return max(0, right_start - left_end)

    def _is_cjk_tail(self, char: str) -> bool:
        """判断末尾字符是否为中日韩文字，避免合并时插入多余空格"""
        return bool(re.match(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", char or ""))

    def _is_cjk_head(self, char: str) -> bool:
        """判断开头字符是否为中日韩文字，避免合并时插入多余空格"""
        return self._is_cjk_tail(char)

    def _beam_size(self) -> int:
        """读取识别 beam size，GPU 默认更准，CPU 默认更快"""
        configured = os.environ.get("YTV_ASR_BEAM_SIZE")
        if configured:
            try:
                return max(1, min(8, int(configured)))
            except ValueError:
                pass
        return 5 if self.device == "cuda" else 3

    def _vad_filter_enabled(self) -> bool:
        """读取 VAD 开关；默认开启但降低阈值，尽量保留细声细语"""
        configured = str(os.environ.get("YTV_ASR_VAD_FILTER") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _vad_parameters(self) -> dict[str, int | float]:
        """生成更适合短视频低音量说话的 VAD 参数"""
        return {
            "threshold": self._env_float("YTV_ASR_VAD_THRESHOLD", 0.3, 0.05, 0.9),
            "min_speech_duration_ms": self._env_int("YTV_ASR_VAD_MIN_SPEECH_MS", 80, 20, 1000),
            "min_silence_duration_ms": self._env_int("YTV_ASR_VAD_MIN_SILENCE_MS", 250, 80, 2000),
            # 垫片过大字幕会比声音早出，200ms 在保留词首和音画同步之间取平衡
            "speech_pad_ms": self._env_int("YTV_ASR_VAD_SPEECH_PAD_MS", 200, 0, 1000),
        }

    def _no_speech_threshold(self) -> float:
        """放宽 Whisper 的静音判定，避免低声句子被当成无语音跳过"""
        return self._env_float("YTV_ASR_NO_SPEECH_THRESHOLD", 0.8, 0.1, 1.0)

    def _env_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """读取整数环境变量，并限制在安全范围内"""
        try:
            value = int(os.environ.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _env_float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        """读取浮点环境变量，并限制在安全范围内"""
        try:
            value = float(os.environ.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

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
